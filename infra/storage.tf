# Cache for the NetCDF files pulled from the Argo GDAC, so a restart doesn't
# re-download them. Bucket names are global, hence the account id.
# https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket
resource "aws_s3_bucket" "cache" {
  bucket = "argo-float-monitor-cache-${data.aws_caller_identity.current.account_id}"
}

# Keep the cache private.
# https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_public_access_block
resource "aws_s3_bucket_public_access_block" "cache" {
  bucket = aws_s3_bucket.cache.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Where the app image lives. deploy.yml pushes here; ECR_REPOSITORY in the
# workflow must match.
# https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/ecr_repository
resource "aws_ecr_repository" "app" {
  name                 = "argo-float-monitor"
  image_tag_mutability = "MUTABLE" # :latest gets overwritten on every push

  image_scanning_configuration {
    scan_on_push = true
  }
}
