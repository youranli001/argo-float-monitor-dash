output "service_url" {
  value = "https://${aws_apprunner_service.app.service_url}"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "s3_cache_bucket" {
  value = aws_s3_bucket.cache.bucket
}

output "github_deploy_role_arn" {
  value = var.github_repo == "" ? null : aws_iam_role.github_deploy[0].arn
}
