variable "region" {
  description = "AWS region for everything except CloudFront (global)."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for all resources."
  type        = string
  default     = "vindex"
}

variable "existing_media_bucket" {
  description = "Adopt this existing S3 bucket for media instead of creating one (Terraform never deletes it)."
  type        = string
  default     = ""
}

variable "image_tag" {
  description = "Tag of the app image in ECR used by ECS services and the state host."
  type        = string
  default     = "latest"
}

# ---------- State host (Postgres, Redis, Qdrant, dispatcher, maintenance) ----------

variable "state_instance_type" {
  description = "Instance for the stateful services. t3.small runs them with a swap file; use t3.medium for headroom."
  type        = string
  default     = "t3.small"
}

variable "state_data_volume_gb" {
  description = "Size of the persistent EBS volume holding Postgres, Redis and Qdrant data."
  type        = number
  default     = 20
}

variable "qdrant_image" {
  description = "Qdrant container image."
  type        = string
  default     = "qdrant/qdrant:latest"
}

variable "snapshot_retention_days" {
  description = "Daily EBS snapshots of the data volume to keep."
  type        = number
  default     = 7
}

# ---------- ECS ----------

variable "api_cpu" {
  type    = number
  default = 512
}

variable "api_memory" {
  description = "The API loads the embedding model for query encoding, so it needs more than the minimum."
  type        = number
  default     = 2048
}

variable "api_min_count" {
  type    = number
  default = 1
}

variable "api_max_count" {
  type    = number
  default = 3
}

variable "search_worker_cpu" {
  type    = number
  default = 2048
}

variable "search_worker_memory" {
  type    = number
  default = 4096
}

variable "playback_worker_cpu" {
  type    = number
  default = 1024
}

variable "playback_worker_memory" {
  type    = number
  default = 2048
}

variable "worker_max_count" {
  description = "Upper bound for each worker service. Workers scale down to zero when idle."
  type        = number
  default     = 4
}

variable "worker_use_spot" {
  description = "Run workers on Fargate Spot (about 70% cheaper; interrupted tasks are retried by the pipeline)."
  type        = bool
  default     = true
}

variable "container_insights" {
  description = "Enable ECS Container Insights (per-task CPU/memory metrics; adds a few dollars a month)."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  type    = number
  default = 14
}

# ---------- Application settings ----------

variable "whisper_model" {
  description = "Must match the model baked into the image (Dockerfile WHISPER_MODEL)."
  type        = string
  default     = "tiny"
}

variable "max_upload_mb" {
  type    = number
  default = 500
}

variable "cognito_user_pool_id" {
  description = "Existing Cognito user pool. Empty disables auth (everyone acts as one dev user) - only for a private test."
  type        = string
  default     = ""
}

variable "cognito_app_client_id" {
  type    = string
  default = ""
}

variable "extra_upload_origins" {
  description = "Extra origins allowed to upload straight to the media bucket, e.g. [\"http://localhost:5173\"] for local frontend dev."
  type        = list(string)
  default     = []
}

# ---------- Alerts ----------

variable "alarm_email" {
  description = "Email for CloudWatch alarm notifications (confirm the subscription email AWS sends). Empty disables notifications."
  type        = string
  default     = ""
}

variable "api_throttle_rate" {
  description = "API Gateway steady-state requests per second; protects the demo bill from floods."
  type        = number
  default     = 50
}
