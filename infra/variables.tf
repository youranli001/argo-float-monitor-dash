variable "aws_region" {
  type    = string
  default = "us-west-2"
}

variable "project" {
  type    = string
  default = "argo-float-monitor"
}

variable "cache_ttl_days" {
  type    = number
  default = 7
}

variable "github_repo" {
  type    = string
  default = ""
}

variable "github_branch" {
  type    = string
  default = "main"
}
