# Alarms notify by email when alarm_email is set. Pipeline health metrics come
# from the state host (same values the maintenance /metrics endpoint serves).

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  pipeline_alarms = {
    "pipeline-stalled" = {
      description = "Work has waited more than 30 minutes to start."
      metric      = "pending_age_seconds"
      threshold   = 1800
      periods     = 1
    }
    "leases-expired" = {
      description = "Processing leases expired for 15 minutes (workers crashed or were stopped mid-task)."
      metric      = "expired_work"
      threshold   = 1
      periods     = 15
    }
    "processing-failures" = {
      description = "Videos or chunks failed permanently in the last 10 minutes."
      metric      = "recent_failures"
      threshold   = 1
      periods     = 1
    }
    "dispatcher-down" = {
      description = "The outbox dispatcher hasn't completed a pass in 5 minutes."
      metric      = "dispatcher_success_age_seconds"
      threshold   = 300
      periods     = 3
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "pipeline" {
  for_each            = local.pipeline_alarms
  alarm_name          = "${local.name}-${each.key}"
  alarm_description   = each.value.description
  namespace           = local.metrics_namespace
  metric_name         = each.value.metric
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = each.value.periods
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = each.value.threshold
  # Missing data means the demo is switched off, not broken.
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
  ok_actions         = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${local.name}-api-5xx"
  alarm_description   = "API returned 5 or more server errors in 5 minutes."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  dimensions          = { ApiId = aws_apigatewayv2_api.main.id }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 5
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "state_host_unhealthy" {
  alarm_name          = "${local.name}-state-host-status"
  alarm_description   = "State host failed a status check (auto-recovery handles hardware failures)."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  dimensions          = { InstanceId = aws_instance.state.id }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title  = "Pipeline backlog by queue"
          region = var.region
          stat   = "Maximum"
          period = 60
          metrics = [
            [local.metrics_namespace, "Backlog", "Queue", "search"],
            [local.metrics_namespace, "Backlog", "Queue", "playback"],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title  = "Worker tasks (task counts need container_insights = true)"
          region = var.region
          stat   = "Maximum"
          period = 60
          metrics = [
            ["ECS/ContainerInsights", "DesiredTaskCount", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.search_worker.name],
            ["ECS/ContainerInsights", "DesiredTaskCount", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.playback_worker.name],
            [local.metrics_namespace, "BacklogPerTask", "Queue", "search", { label = "search backlog per task" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title  = "API requests, errors, latency"
          region = var.region
          period = 300
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiId", aws_apigatewayv2_api.main.id, { stat = "Sum" }],
            ["AWS/ApiGateway", "5xx", "ApiId", aws_apigatewayv2_api.main.id, { stat = "Sum" }],
            ["AWS/ApiGateway", "Latency", "ApiId", aws_apigatewayv2_api.main.id, { stat = "p90", yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title  = "Pipeline health"
          region = var.region
          stat   = "Maximum"
          period = 60
          metrics = [
            [local.metrics_namespace, "pending_age_seconds"],
            [local.metrics_namespace, "expired_work", { yAxis = "right" }],
            [local.metrics_namespace, "recent_failures", { yAxis = "right" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 12, width = 24, height = 6
        properties = {
          title  = "State host and services CPU"
          region = var.region
          stat   = "Average"
          period = 300
          metrics = [
            ["AWS/EC2", "CPUUtilization", "InstanceId", aws_instance.state.id, { label = "state host" }],
            ["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.api.name, { label = "api" }],
            ["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", aws_ecs_service.search_worker.name, { label = "search workers" }],
          ]
        }
      },
    ]
  })
}
