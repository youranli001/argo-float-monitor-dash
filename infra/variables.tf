variable "aws_region" {
  type    = string
  default = "us-west-2"
}

variable "project" {
  type    = string
  default = "argo-float-monitor"
}

variable "alert_email" {
  type = string
}

variable "monthly_budget_usd" {
  type    = number
  default = 15
}

variable "instance_cpu" {
  type    = string
  default = "1024"
}

variable "instance_memory" {
  type    = string
  default = "2048"
}

variable "max_instances" {
  type    = number
  default = 2
}

variable "cache_ttl_days" {
  type    = number
  default = 7
}

variable "cache_expiry_days" {
  type    = number
  default = 30
}

variable "github_repo" {
  type    = string
  default = ""
}

variable "github_branch" {
  type    = string
  default = "main"
}
