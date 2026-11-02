output "app_url" {
  description = "Public URL of the app (frontend and API on one domain)."
  value       = "https://${aws_cloudfront_distribution.main.domain_name}"
}

output "region" {
  value = var.region
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "media_bucket" {
  value = local.media_bucket_name
}

output "frontend_bucket" {
  value = aws_s3_bucket.frontend.bucket
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.main.id
}

output "api_gateway_url" {
  description = "Direct API Gateway URL (the app normally goes through app_url)."
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "ecs_services" {
  value = {
    api             = aws_ecs_service.api.name
    search_worker   = aws_ecs_service.search_worker.name
    playback_worker = aws_ecs_service.playback_worker.name
  }
}

output "state_instance_id" {
  value = aws_instance.state.id
}

output "dashboard_url" {
  value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${aws_cloudwatch_dashboard.main.dashboard_name}"
}
