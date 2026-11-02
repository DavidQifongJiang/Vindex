#!/usr/bin/env bash
# Ship the current checkout to AWS: image -> ECR, state host (migrations,
# dispatcher, maintenance, metrics), ECS services, then the frontend.
#
# Usage (from the repo root, after `terraform apply`):
#   infra/scripts/deploy.sh [image-tag]        # default tag: current git commit
#   SKIP_FRONTEND=1 infra/scripts/deploy.sh    # backend only
#
# Needs: aws CLI v2 (logged in), docker, terraform, node 22. On Windows run it
# from Git Bash or WSL.
set -euo pipefail
cd "$(dirname "$0")/../.."

tf_out() { terraform -chdir=infra/terraform output -raw "$1"; }

TAG="${1:-$(git rev-parse --short HEAD)}"
REGION="$(tf_out region)"
REPO="$(tf_out ecr_repository_url)"
CLUSTER="$(tf_out ecs_cluster)"
STATE_INSTANCE="$(tf_out state_instance_id)"
APP_URL="$(tf_out app_url)"
SERVICES="$(terraform -chdir=infra/terraform output -json ecs_services | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin).values()))')"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

echo "==> Building and pushing $REPO:$TAG"
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --platform linux/amd64 -t "$REPO:$TAG" -t "$REPO:latest" .
docker push "$REPO:$TAG"
docker push "$REPO:latest"

echo "==> Updating the state host (runs migrations before restarting the dispatcher)"
COMMAND_ID="$(aws ssm send-command --instance-ids "$STATE_INSTANCE" --document-name AWS-RunShellScript \
  --comment "vindex deploy $TAG" --parameters "commands=[\"/opt/vindex/deploy.sh $TAG\"]" \
  --query Command.CommandId --output text)"
if ! aws ssm wait command-executed --command-id "$COMMAND_ID" --instance-id "$STATE_INSTANCE"; then
  aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id "$STATE_INSTANCE" \
    --query '[StatusDetails, StandardErrorContent]' --output text
  echo "State host deploy failed; ECS services were not updated." >&2
  exit 1
fi
aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id "$STATE_INSTANCE" \
  --query StandardOutputContent --output text | tail -3

echo "==> Rolling ECS services onto the new image"
for service in $SERVICES; do
  aws ecs update-service --cluster "$CLUSTER" --service "$service" --force-new-deployment \
    --query 'service.serviceName' --output text
done

if [ -z "${SKIP_FRONTEND:-}" ]; then
  echo "==> Building and publishing the frontend"
  # Cognito settings for the build; see infra/frontend.env.example.
  if [ -f infra/frontend.env ]; then set -a; . infra/frontend.env; set +a; fi
  export VITE_UPLOAD_MODE=direct VITE_API_BASE_URL=""
  export VITE_COGNITO_REDIRECT_URI="${VITE_COGNITO_REDIRECT_URI:-$APP_URL/}"
  (cd frontend && npm ci && npm run build)
  BUCKET="$(tf_out frontend_bucket)"
  # Hashed assets cache for a year; index.html must always be revalidated.
  aws s3 sync frontend/dist "s3://$BUCKET" --delete --exclude index.html \
    --cache-control "public, max-age=31536000, immutable"
  aws s3 cp frontend/dist/index.html "s3://$BUCKET/index.html" --cache-control "no-cache"
  aws cloudfront create-invalidation --distribution-id "$(tf_out cloudfront_distribution_id)" \
    --paths /index.html --query Invalidation.Id --output text
fi

echo "==> Deployed $TAG -> $APP_URL"
echo "    ECS rollout continues in the background: aws ecs wait services-stable --cluster $CLUSTER --services $SERVICES"
