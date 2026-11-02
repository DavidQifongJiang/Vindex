locals {
  api_name             = "${local.name}-api"
  search_worker_name   = "${local.name}-search-worker"
  playback_worker_name = "${local.name}-playback-worker"

  app_environment = [
    for name, value in {
      STORAGE_BACKEND           = "s3"
      S3_BUCKET_NAME            = aws_s3_bucket.media.bucket
      AWS_REGION                = var.region
      QDRANT_URL                = "http://${local.state_host}:6333"
      MAX_UPLOAD_MB             = tostring(var.max_upload_mb)
      RUN_MIGRATIONS_ON_STARTUP = "false"
      WHISPER_MODEL             = var.whisper_model
      EMBEDDING_MODEL           = "all-MiniLM-L6-v2"
      # Models are baked into the image; never reach out to the hub at runtime.
      HF_HUB_OFFLINE       = "1"
      TRANSFORMERS_OFFLINE = "1"
      AUTH_REQUIRED         = local.auth_on ? "true" : "false"
      COGNITO_REGION        = var.region
      COGNITO_USER_POOL_ID  = var.cognito_user_pool_id
      COGNITO_APP_CLIENT_ID = var.cognito_app_client_id
    } : { name = name, value = value }
  ]

  app_secrets = [
    for name, key in {
      DATABASE_URL          = "database-url"
      CELERY_BROKER_URL     = "broker-url"
      CELERY_RESULT_BACKEND = "result-backend-url"
      REDIS_URL             = "broker-url"
      QDRANT_API_KEY        = "qdrant-api-key"
    } : { name = name, valueFrom = aws_ssm_parameter.secret[key].arn }
  ]

  worker_threads = {
    search   = tostring(max(1, floor(var.search_worker_cpu / 1024)))
    playback = tostring(max(1, floor(var.playback_worker_cpu / 1024)))
  }

  worker_capacity = var.worker_use_spot ? "FARGATE_SPOT" : "FARGATE"
}

resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enabled" : "disabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
}

resource "aws_cloudwatch_log_group" "ecs" {
  for_each          = toset(["api", "search-worker", "playback-worker"])
  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = var.log_retention_days
}

# ---------- IAM ----------

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${local.name}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "task_execution_secrets" {
  statement {
    actions   = ["ssm:GetParameters"]
    resources = [for parameter in aws_ssm_parameter.secret : parameter.arn]
  }
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  name   = "${local.name}-secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.task_execution_secrets.json
}

# What the application itself may do: read and write media, nothing else.
resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "task" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.media.arn}/*"]
  }

  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "${local.name}-media"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# ---------- Task definitions ----------

resource "aws_ecs_task_definition" "api" {
  family                   = local.api_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name         = "api"
    image        = local.image
    essential    = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment  = local.app_environment
    secrets      = local.app_secrets
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)\""]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.ecs["api"].name
        awslogs-region        = var.region
        awslogs-stream-prefix = "api"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "worker" {
  for_each = {
    search = {
      family = local.search_worker_name
      cpu    = var.search_worker_cpu
      memory = var.search_worker_memory
      queues = "search,celery"
    }
    playback = {
      family = local.playback_worker_name
      cpu    = var.playback_worker_cpu
      memory = var.playback_worker_memory
      queues = "playback"
    }
  }

  family                   = each.value.family
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "worker"
    image     = local.image
    essential = true
    command = ["celery", "-A", "app.workers.celery_app.celery_app", "worker", "--loglevel=info",
    "--pool=prefork", "--concurrency=1", "-Q", each.value.queues, "-n", "${each.key}@%h"]
    environment = concat(local.app_environment, [
      for name in ["OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"] :
      { name = name, value = local.worker_threads[each.key] }
    ], [{ name = "TOKENIZERS_PARALLELISM", value = "false" }])
    secrets = local.app_secrets
    # Fargate's maximum: lets Celery finish or hand back the current task on scale-in or Spot reclaim.
    stopTimeout = 120
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.ecs["${each.key}-worker"].name
        awslogs-region        = var.region
        awslogs-stream-prefix = each.key
      }
    }
  }])
}

# ---------- Services ----------

resource "aws_service_discovery_service" "api" {
  name = "api"

  dns_config {
    namespace_id   = aws_service_discovery_private_dns_namespace.main.id
    routing_policy = "MULTIVALUE"

    # API Gateway's Cloud Map integration needs SRV records (address + port).
    dns_records {
      type = "SRV"
      ttl  = 10
    }
  }

  health_check_custom_config {}
}

resource "aws_ecs_service" "api" {
  name                   = local.api_name
  cluster                = aws_ecs_cluster.main.id
  task_definition        = aws_ecs_task_definition.api.arn
  desired_count          = var.api_min_count
  enable_execute_command = true

  capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = true
  }

  service_registries {
    registry_arn   = aws_service_discovery_service.api.arn
    container_name = "api"
    container_port = 8000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_ecs_cluster_capacity_providers.main]
}

resource "aws_ecs_service" "search_worker" {
  name            = local.search_worker_name
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker["search"].arn
  desired_count   = 0

  capacity_provider_strategy {
    capacity_provider = local.worker_capacity
    weight            = 1
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = true
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_ecs_cluster_capacity_providers.main]
}

resource "aws_ecs_service" "playback_worker" {
  name            = local.playback_worker_name
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker["playback"].arn
  desired_count   = 0

  capacity_provider_strategy {
    capacity_provider = local.worker_capacity
    weight            = 1
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = true
  }

  lifecycle {
    ignore_changes = [desired_count]
  }

  depends_on = [aws_ecs_cluster_capacity_providers.main]
}
