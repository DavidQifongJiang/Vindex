# Generated credentials live in SSM Parameter Store (SecureString, free tier)
# and are injected into ECS tasks as secrets and read by the state host at deploy.

resource "random_password" "postgres" {
  length  = 32
  special = false
}

resource "random_password" "redis" {
  length  = 32
  special = false
}

resource "random_password" "qdrant" {
  length  = 32
  special = false
}

locals {
  state_host   = "state.${aws_service_discovery_private_dns_namespace.main.name}"
  param_prefix = "/${local.name}"

  secret_values = {
    "postgres-password"  = random_password.postgres.result
    "redis-password"     = random_password.redis.result
    "qdrant-api-key"     = random_password.qdrant.result
    "database-url"       = "postgresql+psycopg2://vindex:${random_password.postgres.result}@${local.state_host}:5432/vindex"
    "broker-url"         = "redis://:${random_password.redis.result}@${local.state_host}:6379/0"
    "result-backend-url" = "redis://:${random_password.redis.result}@${local.state_host}:6379/1"
  }
}

resource "aws_ssm_parameter" "secret" {
  for_each = local.secret_values
  name     = "${local.param_prefix}/${each.key}"
  type     = "SecureString"
  value    = each.value
}
