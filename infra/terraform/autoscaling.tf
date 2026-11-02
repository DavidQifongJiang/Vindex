# ---------- API: scale on CPU ----------

resource "aws_appautoscaling_target" "api" {
  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.api.name}"
  min_capacity       = var.api_min_count
  max_capacity       = var.api_max_count
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${local.name}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.api.service_namespace
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  resource_id        = aws_appautoscaling_target.api.resource_id

  target_tracking_scaling_policy_configuration {
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

# ---------- Workers: 0 -> N on pipeline backlog, back to 0 when idle ----------
#
# Backlog comes from Postgres (published every minute by the state host's
# pipeline-metrics container). Three policies per worker service:
#   wake  - work is waiting and no task exists: add one task within ~1 minute
#   grow  - target tracking keeps backlog per task near 1 (scale-out only)
#   idle  - backlog has been zero for 5 minutes: scale to zero
# Scale-in only happens at zero backlog, which counts in-flight work, so a
# worker is never stopped mid-transcription by a partial scale-in.

locals {
  worker_services = {
    search   = aws_ecs_service.search_worker.name
    playback = aws_ecs_service.playback_worker.name
  }
}

resource "aws_appautoscaling_target" "worker" {
  for_each           = local.worker_services
  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${each.value}"
  min_capacity       = 0
  max_capacity       = var.worker_max_count
}

resource "aws_appautoscaling_policy" "worker_grow" {
  for_each           = local.worker_services
  name               = "${local.name}-${each.key}-grow"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.worker[each.key].service_namespace
  scalable_dimension = aws_appautoscaling_target.worker[each.key].scalable_dimension
  resource_id        = aws_appautoscaling_target.worker[each.key].resource_id

  target_tracking_scaling_policy_configuration {
    target_value       = 1
    disable_scale_in   = true
    scale_out_cooldown = 60

    customized_metric_specification {
      namespace   = local.metrics_namespace
      metric_name = "BacklogPerTask"
      statistic   = "Maximum"

      dimensions {
        name  = "Queue"
        value = each.key
      }
    }
  }
}

resource "aws_appautoscaling_policy" "worker_wake" {
  for_each           = local.worker_services
  name               = "${local.name}-${each.key}-wake"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.worker[each.key].service_namespace
  scalable_dimension = aws_appautoscaling_target.worker[each.key].scalable_dimension
  resource_id        = aws_appautoscaling_target.worker[each.key].resource_id

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 120
    metric_aggregation_type = "Maximum"

    step_adjustment {
      metric_interval_lower_bound = 0
      scaling_adjustment          = 1
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "worker_wake" {
  for_each            = local.worker_services
  alarm_name          = "${local.name}-${each.key}-worker-needed"
  alarm_description   = "Work is queued for ${each.key} and no worker task exists."
  namespace           = local.metrics_namespace
  metric_name         = "WorkerNeeded"
  dimensions          = { Queue = each.key }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.worker_wake[each.key].arn]
}

resource "aws_appautoscaling_policy" "worker_idle" {
  for_each           = local.worker_services
  name               = "${local.name}-${each.key}-idle"
  policy_type        = "StepScaling"
  service_namespace  = aws_appautoscaling_target.worker[each.key].service_namespace
  scalable_dimension = aws_appautoscaling_target.worker[each.key].scalable_dimension
  resource_id        = aws_appautoscaling_target.worker[each.key].resource_id

  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 300
    metric_aggregation_type = "Maximum"

    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = 0
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "worker_idle" {
  for_each            = local.worker_services
  alarm_name          = "${local.name}-${each.key}-worker-idle"
  alarm_description   = "No ${each.key} work for 5 minutes: scale workers to zero."
  namespace           = local.metrics_namespace
  metric_name         = "Backlog"
  dimensions          = { Queue = each.key }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  comparison_operator = "LessThanOrEqualToThreshold"
  threshold           = 0
  # No metrics means the state host is off; demo-off scales workers down itself.
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_appautoscaling_policy.worker_idle[each.key].arn]
}
