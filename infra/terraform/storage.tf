# ---------- Media bucket: raw uploads, processed video, transcripts ----------

# Either adopt an existing bucket (existing_media_bucket) or create one. An
# adopted bucket is never deleted by Terraform; only its CORS and lifecycle
# rules are managed here.
resource "aws_s3_bucket" "media" {
  count  = var.existing_media_bucket == "" ? 1 : 0
  bucket = "${local.name}-media-${local.account_id}"
}

data "aws_s3_bucket" "existing_media" {
  count  = var.existing_media_bucket == "" ? 0 : 1
  bucket = var.existing_media_bucket
}

locals {
  media_bucket_name = var.existing_media_bucket == "" ? aws_s3_bucket.media[0].bucket : data.aws_s3_bucket.existing_media[0].bucket
  media_bucket_arn  = var.existing_media_bucket == "" ? aws_s3_bucket.media[0].arn : data.aws_s3_bucket.existing_media[0].arn
}

resource "aws_s3_bucket_public_access_block" "media" {
  # An adopted bucket keeps its own settings; destroy must not loosen them.
  count  = var.existing_media_bucket == "" ? 1 : 0
  bucket                  = local.media_bucket_name
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "media" {
  # An adopted bucket keeps its own settings; destroy must not loosen them.
  count  = var.existing_media_bucket == "" ? 1 : 0
  bucket = local.media_bucket_name
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Browsers POST uploads straight here using presigned policies from the API.
resource "aws_s3_bucket_cors_configuration" "media" {
  bucket = local.media_bucket_name

  cors_rule {
    allowed_methods = ["POST", "GET", "HEAD"]
    allowed_origins = concat(["https://${aws_cloudfront_distribution.main.domain_name}"], var.extra_upload_origins)
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "media" {
  bucket = local.media_bucket_name

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# ---------- Frontend bucket: private, served only through CloudFront ----------

resource "aws_s3_bucket" "frontend" {
  bucket = "${local.name}-frontend-${local.account_id}"
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

data "aws_iam_policy_document" "frontend_bucket" {
  statement {
    sid       = "CloudFrontRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.main.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket     = aws_s3_bucket.frontend.id
  policy     = data.aws_iam_policy_document.frontend_bucket.json
  depends_on = [aws_s3_bucket_public_access_block.frontend]
}

# ---------- Container registry ----------

resource "aws_ecr_repository" "app" {
  name                 = local.name
  image_tag_mutability = "MUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 most recent images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}
