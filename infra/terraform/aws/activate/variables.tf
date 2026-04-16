variable "region" {
  type = string
}

variable "project_name" {
  type    = string
  default = "mssp-pipeline"
}

variable "schedule_expression" {
  type = string
}

variable "enable_schedule" {
  type    = bool
  default = true
}

variable "foundation_state_backend" {
  type    = string
  default = "local"

  validation {
    condition     = contains(["local", "s3"], var.foundation_state_backend)
    error_message = "foundation_state_backend must be one of: local, s3"
  }
}

variable "foundation_state_local_path" {
  type    = string
  default = "../foundation/terraform.tfstate"
}

variable "foundation_state_s3_bucket" {
  type    = string
  default = ""

  validation {
    condition     = var.foundation_state_backend != "s3" || var.foundation_state_s3_bucket != ""
    error_message = "foundation_state_s3_bucket is required when foundation_state_backend = \"s3\""
  }
}

variable "foundation_state_s3_key" {
  type    = string
  default = ""

  validation {
    condition     = var.foundation_state_backend != "s3" || var.foundation_state_s3_key != ""
    error_message = "foundation_state_s3_key is required when foundation_state_backend = \"s3\""
  }
}

variable "foundation_state_s3_region" {
  type    = string
  default = ""

  validation {
    condition     = var.foundation_state_backend != "s3" || var.foundation_state_s3_region != ""
    error_message = "foundation_state_s3_region is required when foundation_state_backend = \"s3\""
  }
}

variable "foundation_state_s3_dynamodb_table" {
  type    = string
  default = ""
}

variable "ecs_cluster_arn" {
  type    = string
  default = ""
}

variable "runtime_task_definition_arn" {
  type = string
}

variable "events_invoke_role_arn" {
  type    = string
  default = ""
}

variable "ecs_subnet_ids" {
  type    = list(string)
  default = []
}

variable "ecs_security_group_ids" {
  type    = list(string)
  default = []
}
