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

## Deploying (GitHub Actions)

`.github/workflows/deploy.yml` deploys `main` after CI passes, or on demand from the Actions tab. It runs `terraform apply` (state in S3), then `infra/scripts/deploy.sh`: it builds and pushes the image, runs migrations and restarts the pipeline processes on the state host, rolls the ECS services, and publishes the frontend. AWS access is a short-lived OIDC session; no AWS keys are stored in GitHub.

Deployment settings that aren't secret are committed: `terraform/vindex.auto.tfvars` (adopted media bucket, Cognito IDs), `terraform/backend.hcl` and `frontend.production.env`.

**One-time setup**

1. **AWS bootstrap** (as an administrator): open AWS CloudShell, upload `infra/bootstrap/github-deploy.sh` and run `bash github-deploy.sh`. It creates the Terraform state bucket, the GitHub OIDC provider and the `vindex-github-deploy` role, which only the `production` environment of this repository can assume.
2. **GitHub environment**: in the repo, go to Settings → Environments → New environment and create `production`. Under Deployment branches and tags, choose "Selected branches and tags" and add `main`. This is what keeps other branches from using the deploy role.
3. Optional: under Settings → Secrets and variables → Actions → Variables, add `ALARM_EMAIL` to get alarm emails. Confirm the subscription email that SNS sends.
4. Run the **Deploy** workflow from the Actions tab. The first run takes about 30 minutes: CloudFront, plus building the roughly 3 GB image.
5. Add the `app_url` from the run summary, with a trailing `/`, to the Cognito app client's callback and sign-out URLs.

**Running Terraform locally** (needs admin credentials):
```bash
cd infra/terraform
terraform init -backend-config=backend.hcl
terraform plan
```
Local runs share the same state and lock as CI. Don't apply locally while a Deploy run is in progress.

## Day to day

| Task | Command |
|---|---|
| Deploy | merge to `main`, or run **Deploy** in the Actions tab |
| Deploy from your machine | `infra/scripts/deploy.sh` (after `terraform init -backend-config=backend.hcl`) |
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

- **Terraform state** lives in `vindex-tfstate-<account>` (private, versioned) and contains the generated passwords.
- **Destroying.** `terraform destroy` deletes the data volume (snapshots remain until retention expires). The adopted media bucket is never deleted; empty the frontend bucket and ECR repository first, or destroy fails on them.
- **Search features.** Elasticsearch, library search and Kafka analytics stay disabled (their flags default off). They need more memory than the state host has.
- **Direct API URL.** `api_gateway_url` is publicly reachable too; it has the same auth and is throttled (`api_throttle_rate`).
- **Images** are x86. Graviton (`cpu_architecture = "ARM64"` plus an arm64 build) is about 20% cheaper on Fargate.
