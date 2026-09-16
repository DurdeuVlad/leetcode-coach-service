variable "aws_region" {
  description = "AWS region for a future deployment."
  type        = string
  default     = "eu-central-1"
}

variable "project_name" {
  description = "Stable prefix for future resources."
  type        = string
  default     = "leetcode-coach"
}

variable "enable_resources" {
  description = "Safety switch. Keep false until the architecture, cost, network, and data-retention review is complete."
  type        = bool
  default     = false
}

variable "vpc_cidr" {
  description = "Private network range for the future service."
  type        = string
  default     = "10.42.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones reserved for the future private subnets."
  type        = list(string)
  default     = ["eu-central-1a", "eu-central-1b"]
}

variable "database_master_username" {
  description = "Future database administrator username. The password must come from Secrets Manager or an equivalent runtime secret."
  type        = string
  default     = "leetcode_coach"
}

variable "container_image" {
  description = "Future immutable application image reference."
  type        = string
  default     = "REPLACE_WITH_IMMUTABLE_IMAGE"
}
