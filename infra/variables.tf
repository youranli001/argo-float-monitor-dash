variable "aws_region" {
  description = "Region for all resources (us-west-2 is closest to San Diego with App Runner support)"
  type        = string
  default     = "us-west-2"
}

variable "project" {
  type    = string
  default = "argo-float-monitor"
}

variable "alert_email" {
  description = "Where AWS Budgets sends cost alerts"
  type        = string
}

variable "monthly_budget_usd" {
  type    = number
  default = 15
}

variable "instance_cpu" {
  description = "App Runner vCPU units: 256, 512, 1024, 2048, 4096"
  type        = string
  default     = "1024"
}

variable "instance_memory" {
  description = "App Runner memory MB: 512, 1024, 2048, 3072, 4096 ..."
  type        = string
  default     = "2048"
}

variable "max_instances" {
  type    = number
  default = 2
}

variable "cache_ttl_days" {
  description = "Re-fetch a float from GDAC if the S3 copy is older than this"
  type        = number
  default     = 7
}

variable "cache_expiry_days" {
  description = "S3 lifecycle: delete cached files untouched for this many days"
  type        = number
  default     = 30
}

variable "github_repo" {
  description = "owner/repo allowed to push images via OIDC; leave empty to skip"
  type        = string
  default     = ""
}

variable "github_branch" {
  type    = string
  default = "main"
}
