# AWS infrastructure

Terraform defines the AWS resources used by the dashboard:

- `storage.tf`: S3 cache and ECR repository
- `iam.tf`: App Runner permissions
- `app.tf`: App Runner service
- `github.tf`: GitHub Actions OIDC deployment role

Run `terraform plan` before applying changes.
