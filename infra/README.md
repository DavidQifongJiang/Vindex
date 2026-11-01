# Deploying Vindex to AWS

Terraform in `terraform/` builds the whole stack; `scripts/` ships code and switches the demo on and off.

```
Browser ─► CloudFront ──► S3 (React build)
                     └──► API Gateway (HTTP API) ─► VPC link ─► Cloud Map ─► ECS Fargate: API
Browser ─► S3 media bucket (direct upload, presigned POST)

State host (EC2 + EBS):  Postgres · Redis · Qdrant · outbox dispatcher · maintenance · backlog metrics
ECS Fargate Spot:         search workers (Whisper + MiniLM)  0 → N
                          playback workers (FFmpeg)          0 → N
```

- **One domain.** CloudFront serves the frontend and forwards API paths (`/videos*`, `/public/*`, `/me`, `/search*`, `/health`) to API Gateway, so the frontend calls relative URLs and there is no CORS between site and API.
- **No ALB, no NAT Gateway.** API Gateway reaches API tasks through a VPC link and Cloud Map. Everything runs in public subnets with public IPs for outbound traffic; security groups are the only way in.
- **Uploads bypass the API.** API Gateway caps requests at 10 MB / 30 s, so the browser posts files straight to S3 (`VITE_UPLOAD_MODE=direct`).
- **State host.** Postgres, Redis and Qdrant share one small EC2 instance with a separate encrypted data volume (snapshotted daily, kept 7 days). The dispatcher polls Postgres every 5 s, which is also why Aurora Serverless couldn't pause. Admin access is through SSM Session Manager; no SSH port is open.

## How workers scale

The state host publishes backlog to CloudWatch (`Vindex/Pipeline`) every minute, counted from Postgres (pending, retrying and processing work), not from Redis.

| Policy | Trigger | Action |
|---|---|---|
| wake | `WorkerNeeded` = 1 (work queued, no task) for 1 min | +1 task |
| grow | `BacklogPerTask` above 1 | target tracking, scale-out only |
| idle | `Backlog` = 0 for 5 min | scale to 0 |

Scale-in only happens at zero backlog, and backlog includes in-flight work, so a worker is never stopped mid-transcription by a partial scale-in. A first upload after idle waits about 1 minute for the alarm plus 1–2 minutes for a Fargate task to pull the image and start.

## First deployment

Prerequisites: AWS CLI v2 logged in to your account, Terraform ≥ 1.6, Docker, Node 22. On Windows, run the scripts from Git Bash or WSL.

1. **Configure.**
   ```bash
   cd infra/terraform
   cp terraform.tfvars.example terraform.tfvars   # Cognito IDs, alarm email
   cp ../frontend.env.example ../frontend.env     # Cognito hosted UI settings for the build
   ```
2. **Create the infrastructure** (about 10 minutes; CloudFront is the slow part).
   ```bash
   terraform init
   terraform plan -out tfplan
   terraform apply tfplan
   ```
   ECS services start with no image yet, so the API service retries until step 3. The state host boots, formats its data volume and starts Postgres, Redis and Qdrant.
3. **Ship the code.** From the repo root:
   ```bash
   infra/scripts/deploy.sh
   ```
   This builds and pushes the image (models baked in, about 3 GB the first time), runs migrations and starts the dispatcher, maintenance and metrics on the state host, rolls the ECS services, and publishes the frontend.
4. **Cognito.** Add the `app_url` output (with a trailing `/`) to your app client's allowed callback and sign-out URLs.
5. **Alarms.** Confirm the subscription email from AWS SNS if you set `alarm_email`.

Open `terraform output app_url` and upload a video. Watch it scale on the dashboard (`terraform output dashboard_url`).

## Day to day

| Task | Command |
|---|---|
| Deploy the current commit | `infra/scripts/deploy.sh` |
| Backend only | `SKIP_FRONTEND=1 infra/scripts/deploy.sh` |
| Switch off (storage cost only) | `infra/scripts/demo.sh off` |
| Switch back on | `infra/scripts/demo.sh on` |
| Shell on the state host | `aws ssm start-session --target $(terraform -chdir=infra/terraform output -raw state_instance_id)` |
| State host logs | CloudWatch log group `/vindex/state-host` (one stream per container) |
| API / worker logs | `/ecs/vindex/api`, `/ecs/vindex/search-worker`, `/ecs/vindex/playback-worker` |

Run `demo.sh on` before `terraform apply`: the off switch suspends autoscaling, and Terraform would try to undo that.

## Cost (us-east-1 list prices)

| Mode | ~Monthly |
|---|---|
| On 24/7 | ~$45–50 (API task ~$22, state host t3.small + EBS ~$18, public IPv4 ~$7, CloudWatch/misc ~$3) |
| Off (`demo.sh off`) | ~$5 (EBS, snapshots, S3, ECR) |
| Processing | a few cents per video on Fargate Spot |

Container Insights (`container_insights = true`) adds a few dollars a month and fills in the task-count graph on the dashboard.

## Notes and limits

- **Terraform state** is local and contains the generated passwords. Keep it private; for shared use, add an S3 backend in `versions.tf`.
- **Destroying.** `terraform destroy` deletes the data volume (snapshots remain until retention expires). Empty the media and frontend buckets and ECR repository first, or destroy fails on them.
- **Search features.** Elasticsearch, library search and Kafka analytics stay disabled (their flags default off). They need more memory than the state host has.
- **Direct API URL.** `api_gateway_url` is publicly reachable too; it has the same auth and is throttled (`api_throttle_rate`).
- **Images** are x86. Graviton (`cpu_architecture = "ARM64"` plus an arm64 build) is about 20% cheaper on Fargate.
