#!/usr/bin/env bash
# One-time setup so GitHub Actions can deploy Vindex without stored AWS keys.
# Run it once as an administrator, e.g. in AWS CloudShell (console, top bar ">_" icon):
#   bash github-deploy.sh
# Creates, if missing:
#   - S3 bucket vindex-tfstate-<account> for Terraform state (private, versioned, encrypted, TLS only)
#   - IAM OIDC identity provider for token.actions.githubusercontent.com
#   - IAM role vindex-github-deploy, assumable only by workflows on the main branch
#     of DavidQifongJiang/Vindex, with AdministratorAccess (Terraform creates IAM
#     roles, VPCs, ECS, CloudFront and more).
# Safe to re-run. To revoke CI access later: aws iam delete-role-policy / detach and delete the role.
set -euo pipefail

REPO="DavidQifongJiang/Vindex"
REGION="us-east-1"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
STATE_BUCKET="vindex-tfstate-$ACCOUNT"
ROLE="vindex-github-deploy"
PROVIDER_ARN="arn:aws:iam::$ACCOUNT:oidc-provider/token.actions.githubusercontent.com"

echo "Account $ACCOUNT"

if aws s3api head-bucket --bucket "$STATE_BUCKET" 2>/dev/null; then
  echo "State bucket $STATE_BUCKET exists"
else
  aws s3api create-bucket --bucket "$STATE_BUCKET" --region "$REGION" >/dev/null
  echo "Created state bucket $STATE_BUCKET"
fi
aws s3api put-public-access-block --bucket "$STATE_BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket "$STATE_BUCKET" --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket "$STATE_BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'
aws s3api put-bucket-policy --bucket "$STATE_BUCKET" --policy "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Sid\":\"DenyInsecureTransport\",\"Effect\":\"Deny\",\"Principal\":\"*\",\"Action\":\"s3:*\",\"Resource\":[\"arn:aws:s3:::$STATE_BUCKET\",\"arn:aws:s3:::$STATE_BUCKET/*\"],\"Condition\":{\"Bool\":{\"aws:SecureTransport\":\"false\"}}}]}"

if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$PROVIDER_ARN" >/dev/null 2>&1; then
  echo "GitHub OIDC provider exists"
else
  aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 >/dev/null
  echo "Created GitHub OIDC provider"
fi

TRUST="$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
 "Principal":{"Federated":"$PROVIDER_ARN"},"Action":"sts:AssumeRoleWithWebIdentity",
 "Condition":{"StringEquals":{"token.actions.githubusercontent.com:aud":"sts.amazonaws.com",
   "token.actions.githubusercontent.com:sub":"repo:$REPO:environment:production"}}}]}
JSON
)"
if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$ROLE" --policy-document "$TRUST"
  echo "Updated trust policy of $ROLE"
else
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document "$TRUST" --max-session-duration 7200 \
    --description "GitHub Actions deploys of $REPO (production environment only)" >/dev/null
  echo "Created role $ROLE"
fi
aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/AdministratorAccess

echo
echo "Done. Role: arn:aws:iam::$ACCOUNT:role/$ROLE"
