# Remote state for the shared deployment (used by CI and by local runs):
#   terraform init -backend-config=backend.hcl
# The bucket is created once by infra/bootstrap/github-deploy.sh.
bucket       = "vindex-tfstate-585572506964"
key          = "vindex/terraform.tfstate"
region       = "us-east-1"
encrypt      = true
use_lockfile = true
