# Terraform infrastructure

This directory contains the AWS infrastructure for the Argo dashboard.

- `storage.tf`: S3 cache and ECR repository
- `iam.tf`: permissions for App Runner
- `app.tf`: App Runner service
- `github.tf`: optional GitHub Actions OIDC access

`terraform.tfstate`, `terraform.tfvars`, and `.terraform/` stay local. The provider lock file should be committed.
