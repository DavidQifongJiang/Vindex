#!/usr/bin/env bash
# Switch the deployed demo off (pay only for storage) or back on.
#
#   infra/scripts/demo.sh off   # API + workers to 0, stop the state host
#   infra/scripts/demo.sh on    # start the state host, API back to its minimum
#
# Autoscaling is suspended while off so it can't start tasks against a stopped
# database. Run `demo.sh on` before `terraform apply`, which would otherwise
# try to undo the suspension.
set -euo pipefail
cd "$(dirname "$0")/../.."

tf_out() { terraform -chdir=infra/terraform output -raw "$1"; }

ACTION="${1:-}"
REGION="$(tf_out region)"
CLUSTER="$(tf_out ecs_cluster)"
STATE_INSTANCE="$(tf_out state_instance_id)"
SERVICES="$(terraform -chdir=infra/terraform output -json ecs_services | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin).values()))')"
API_SERVICE="$(terraform -chdir=infra/terraform output -json ecs_services | python3 -c 'import json,sys; print(json.load(sys.stdin)["api"])')"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

set_scaling() {
  local suspended=$1 service
  for service in $SERVICES; do
    aws application-autoscaling register-scalable-target --service-namespace ecs \
      --scalable-dimension ecs:service:DesiredCount --resource-id "service/$CLUSTER/$service" \
      --suspended-state "DynamicScalingInSuspended=$suspended,DynamicScalingOutSuspended=$suspended,ScheduledScalingSuspended=$suspended"
  done
}

case "$ACTION" in
  off)
    set_scaling true
    for service in $SERVICES; do
      aws ecs update-service --cluster "$CLUSTER" --service "$service" --desired-count 0 \
        --query 'service.serviceName' --output text
    done
    aws ec2 stop-instances --instance-ids "$STATE_INSTANCE" --query 'StoppingInstances[0].CurrentState.Name' --output text
    echo "Demo is off. Remaining cost: storage (EBS, S3, snapshots, ECR) and CloudFront/API Gateway only when used."
    ;;
  on)
    aws ec2 start-instances --instance-ids "$STATE_INSTANCE" --query 'StartingInstances[0].CurrentState.Name' --output text
    aws ec2 wait instance-running --instance-ids "$STATE_INSTANCE"
    MIN_API="$(aws application-autoscaling describe-scalable-targets --service-namespace ecs \
      --resource-ids "service/$CLUSTER/$API_SERVICE" --query 'ScalableTargets[0].MinCapacity' --output text)"
    aws ecs update-service --cluster "$CLUSTER" --service "$API_SERVICE" --desired-count "$MIN_API" \
      --query 'service.serviceName' --output text
    set_scaling false
    echo "Demo is starting. Containers on the state host restart on boot; the API is ready in ~2 minutes at $(tf_out app_url)"
    ;;
  *)
    echo "Usage: $0 on|off" >&2
    exit 2
    ;;
esac
