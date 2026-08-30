########################################################################
# Argo Float Monitor — AWS infrastructure
#
#   S3 (NetCDF cache)  ←→  App Runner (Dash container from ECR)
#   GitHub Actions ──OIDC──▶ ECR push ──▶ App Runner auto-deploy
#
# Two-phase apply (App Runner needs an image in ECR before it can start):
#   1. terraform apply -target=aws_ecr_repository.app -target=aws_s3_bucket.cache
#   2. build & push the image (see DEPLOY.md)
#   3. terraform apply
########################################################################

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project   = "argo-float-monitor"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

########################################################################
# S3 — durable NetCDF cache
########################################################################
resource "aws_s3_bucket" "cache" {
  bucket        = "${var.project}-cache-${data.aws_caller_identity.current.account_id}"
  force_destroy = true # cache only; safe to destroy
}

resource "aws_s3_bucket_public_access_block" "cache" {
  bucket                  = aws_s3_bucket.cache.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "cache" {
  bucket = aws_s3_bucket.cache.id
  versioning_configuration { status = "Disabled" }
}

# Floats nobody has looked at for a while age out — keeps storage cost ~0.
resource "aws_s3_bucket_lifecycle_configuration" "cache" {
  bucket = aws_s3_bucket.cache.id
  rule {
    id     = "expire-stale-cache"
    status = "Enabled"
    filter { prefix = "gdac-cache/" }
    expiration { days = var.cache_expiry_days }
  }
}

########################################################################
# ECR — container registry
########################################################################
resource "aws_ecr_repository" "app" {
  name                 = var.project
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration { scan_on_push = true }
}

# Keep only the last 5 images so the registry doesn't accumulate cost.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

########################################################################
# IAM — two roles App Runner needs
########################################################################
# (a) access role: lets the App Runner *service* pull from ECR
resource "aws_iam_role" "apprunner_access" {
  name = "${var.project}-apprunner-access"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "build.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "apprunner_ecr" {
  role       = aws_iam_role.apprunner_access.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# (b) instance role: what the *running container* is allowed to do (S3 only)
resource "aws_iam_role" "apprunner_instance" {
  name = "${var.project}-apprunner-instance"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "tasks.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "instance_s3" {
  name = "s3-cache-rw"
  role = aws_iam_role.apprunner_instance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.cache.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.cache.arn}/*"
      }
    ]
  })
}

########################################################################
# App Runner — runs the Dash container
########################################################################
resource "aws_apprunner_auto_scaling_configuration_version" "app" {
  auto_scaling_configuration_name = "${var.project}-asc"
  min_size        = 1
  max_size        = var.max_instances
  max_concurrency = 50
}

resource "aws_apprunner_service" "app" {
  service_name = var.project

  source_configuration {
    auto_deployments_enabled = true # new :latest in ECR ⇒ rolling deploy
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access.arn
    }
    image_repository {
      image_repository_type = "ECR"
      image_identifier      = "${aws_ecr_repository.app.repository_url}:latest"
      image_configuration {
        port = "8080"
        runtime_environment_variables = {
          ARGO_S3_BUCKET      = aws_s3_bucket.cache.bucket
          ARGO_S3_PREFIX      = "gdac-cache"
          ARGO_CACHE_TTL_DAYS = tostring(var.cache_ttl_days)
          ARGO_DATA_DIR       = "/tmp/argo_data"
        }
      }
    }
  }

  instance_configuration {
    cpu               = var.instance_cpu    # "1024" = 1 vCPU
    memory            = var.instance_memory # "2048" = 2 GB
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/healthz"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.app.arn

  depends_on = [aws_iam_role_policy_attachment.apprunner_ecr]
}

########################################################################
# Budget alarm — the first thing to set up on any personal AWS account
########################################################################
resource "aws_budgets_budget" "monthly" {
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

########################################################################
# GitHub Actions → AWS via OIDC (no long-lived access keys in GitHub)
########################################################################
resource "aws_iam_openid_connect_provider" "github" {
  count           = var.github_repo == "" ? 0 : 1
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

resource "aws_iam_role" "github_deploy" {
  count = var.github_repo == "" ? 0 : 1
  name  = "${var.project}-github-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github[0].arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        StringLike   = { "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo}:ref:refs/heads/${var.github_branch}" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_ecr_push" {
  count = var.github_repo == "" ? 0 : 1
  name  = "ecr-push"
  role  = aws_iam_role.github_deploy[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
          "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"
        ]
        Resource = aws_ecr_repository.app.arn
      }
    ]
  })
}
