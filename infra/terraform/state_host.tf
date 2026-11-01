# One small EC2 instance runs everything stateful plus the always-on pipeline
# processes: Postgres, Redis, Qdrant, the outbox dispatcher, maintenance and
# the backlog metrics publisher. Data lives on a separate EBS volume that
# survives instance replacement and is snapshotted daily.

data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_cloudwatch_log_group" "state" {
  name              = "/${local.name}/state-host"
  retention_in_days = var.log_retention_days
}

resource "aws_ebs_volume" "state_data" {
  availability_zone = aws_subnet.public[0].availability_zone
  size              = var.state_data_volume_gb
  type              = "gp3"
  encrypted         = true

  tags = {
    Name     = "${local.name}-state-data"
    Snapshot = "${local.name}-daily"
  }
}

# ---------- IAM ----------

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "state_host" {
  name               = "${local.name}-state-host"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "state_ssm" {
  role       = aws_iam_role.state_host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "state_ecr" {
  role       = aws_iam_role.state_host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

data "aws_iam_policy_document" "state_host" {
  statement {
    sid       = "ReadSecrets"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [for parameter in aws_ssm_parameter.secret : parameter.arn]
  }

  statement {
    sid       = "ContainerLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["${aws_cloudwatch_log_group.state.arn}:*"]
  }

  statement {
    sid       = "PipelineMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [local.metrics_namespace]
    }
  }

  statement {
    sid       = "WorkerCounts"
    actions   = ["ecs:DescribeServices"]
    resources = [aws_ecs_service.search_worker.id, aws_ecs_service.playback_worker.id]
  }

  # Maintenance garbage-collects orphaned artifacts and repairs indexes.
  statement {
    sid       = "MediaBucket"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.media.arn}/*"]
  }

  statement {
    sid       = "MediaBucketList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
  }
}

resource "aws_iam_role_policy" "state_host" {
  name   = "${local.name}-state-host"
  role   = aws_iam_role.state_host.id
  policy = data.aws_iam_policy_document.state_host.json
}

resource "aws_iam_instance_profile" "state_host" {
  name = "${local.name}-state-host"
  role = aws_iam_role.state_host.name
}

# ---------- Instance ----------

locals {
  metrics_namespace = "Vindex/Pipeline"

  state_user_data = templatefile("${path.module}/templates/state-host.sh.tftpl", {
    region                   = var.region
    volume_id                = aws_ebs_volume.state_data.id
    registry                 = split("/", aws_ecr_repository.app.repository_url)[0]
    image_repo               = aws_ecr_repository.app.repository_url
    image_tag                = var.image_tag
    qdrant_image             = var.qdrant_image
    log_group                = aws_cloudwatch_log_group.state.name
    param_prefix             = local.param_prefix
    media_bucket             = aws_s3_bucket.media.bucket
    max_upload_mb            = var.max_upload_mb
    ecs_cluster              = aws_ecs_cluster.main.name
    search_worker_service    = local.search_worker_name
    playback_worker_service  = local.playback_worker_name
    metrics_namespace        = local.metrics_namespace
  })
}

resource "aws_instance" "state" {
  ami                    = data.aws_ssm_parameter.al2023_ami.value
  instance_type          = var.state_instance_type
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.state.id]
  iam_instance_profile   = aws_iam_instance_profile.state_host.name
  user_data              = local.state_user_data

  # Containers reach instance credentials through IMDSv2, which needs one extra hop.
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 30
    encrypted   = true
  }

  tags = { Name = "${local.name}-state" }

  lifecycle {
    # A newer AMI shouldn't replace the host on every apply.
    ignore_changes = [ami]
  }
}

resource "aws_volume_attachment" "state_data" {
  device_name                    = "/dev/sdf"
  volume_id                      = aws_ebs_volume.state_data.id
  instance_id                    = aws_instance.state.id
  stop_instance_before_detaching = true
}

resource "aws_route53_record" "state" {
  zone_id = aws_service_discovery_private_dns_namespace.main.hosted_zone
  name    = local.state_host
  type    = "A"
  ttl     = 60
  records = [aws_instance.state.private_ip]
}

# Replace failed hardware automatically (the data volume reattaches).
resource "aws_cloudwatch_metric_alarm" "state_recover" {
  alarm_name          = "${local.name}-state-host-recover"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  dimensions          = { InstanceId = aws_instance.state.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  alarm_actions       = ["arn:aws:automate:${var.region}:ec2:recover"]
}

# ---------- Daily snapshots of the data volume ----------

data "aws_iam_policy_document" "dlm_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dlm" {
  name               = "${local.name}-dlm"
  assume_role_policy = data.aws_iam_policy_document.dlm_assume.json
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "state_data" {
  description        = "${local.name} state data daily snapshots"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { Snapshot = "${local.name}-daily" }

    schedule {
      name      = "daily"
      copy_tags = true

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["07:00"]
      }

      retain_rule {
        count = var.snapshot_retention_days
      }
    }
  }
}
