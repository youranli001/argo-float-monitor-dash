# 1-2 instances; App Runner adds one past 50 concurrent requests.
# https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/apprunner_auto_scaling_configuration_version
resource "aws_apprunner_auto_scaling_configuration_version" "app" {
  auto_scaling_configuration_name = "argo-float-monitor-asc"

  max_concurrency = 50
  max_size        = 2
  min_size        = 1
}

# The dashboard itself: App Runner runs the container from ECR and gives it
# a public URL.
# https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/apprunner_service
resource "aws_apprunner_service" "app" {
  service_name = "argo-float-monitor"

  source_configuration {
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access.arn
    }
    image_repository {
      image_configuration {
        port = "8080" # see Dockerfile

        # read by argo_storage.py and app.py
        runtime_environment_variables = {
          ARGO_S3_BUCKET      = aws_s3_bucket.cache.bucket
          ARGO_S3_PREFIX      = "gdac-cache"
          ARGO_CACHE_TTL_DAYS = "7"
          ARGO_DATA_DIR       = "/tmp/argo_data"
        }
      }
      image_identifier      = "${aws_ecr_repository.app.repository_url}:latest"
      image_repository_type = "ECR"
    }
    auto_deployments_enabled = true # a new :latest image deploys itself
  }

  instance_configuration {
    cpu               = "1024"
    memory            = "2048"
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/healthz" # app.py
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.app.arn

  # App Runner pulls the image while creating the service, so the ECR
  # permission has to be attached first.
  depends_on = [aws_iam_role_policy_attachment.apprunner_ecr]
}
