resource "aws_apprunner_auto_scaling_configuration_version" "app" {
  auto_scaling_configuration_name = "${var.project}-asc"
  min_size                        = 1
  max_size                        = 2
  max_concurrency                 = 50
}

resource "aws_apprunner_service" "app" {
  service_name = var.project

  source_configuration {
    auto_deployments_enabled = true

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
    cpu               = "1024"
    memory            = "2048"
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
  depends_on                     = [aws_iam_role_policy_attachment.apprunner_ecr]
}
