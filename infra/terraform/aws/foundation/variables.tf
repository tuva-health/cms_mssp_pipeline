variable "region" {
  type = string
}

variable "project_name" {
  type    = string
  default = "mssp-pipeline"
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "runtime_s3_resource_arns" {
  type        = list(string)
  description = "S3 ARNs the runtime task role may list, read and write. Scope to the file store bucket. Required: there is no safe default, since a wildcard grants the task role access to every bucket in the account."

  validation {
    condition     = length(var.runtime_s3_resource_arns) > 0
    error_message = "Set runtime_s3_resource_arns to the file store bucket ARNs, for example [\"arn:aws:s3:::my-bucket\", \"arn:aws:s3:::my-bucket/*\"]."
  }
}

variable "snowflake_rsa_key_secret_name" {
  type        = string
  description = "Optional Secrets Manager secret name for Snowflake RSA private key material used by the runtime task."
  default     = ""
}

variable "snowflake_rsa_key_passphrase_secret_name" {
  type        = string
  description = "Optional Secrets Manager secret name for Snowflake RSA key passphrase used by the runtime task."
  default     = ""
}

variable "allowed_account_ids" {
  type        = list(string)
  description = "If set, the AWS provider refuses to operate outside these account ids. A client overlay pins its own account here."
  default     = []
}

variable "log_retention_days" {
  type        = number
  description = "Retention for the ECS log group."
  default     = 30
}

variable "data_bucket_name" {
  type        = string
  description = "Optional existing/created ACO data bucket to harden and grant the runtime access to. Empty disables the managed data bucket."
  default     = ""
}

variable "create_ecr_repositories" {
  type        = bool
  description = "Create immutable, scan-on-push ECR repositories for the pipeline and connector images."
  default     = true
}

variable "pipeline_repository_name" {
  type        = string
  description = "ECR repository name for the pipeline image."
  default     = "mssp-pipeline"
}

variable "connector_repository_name" {
  type        = string
  description = "ECR repository name for the connector image."
  default     = "mssp-connector"
}

variable "readiness_execution_role_names" {
  type        = list(string)
  description = "Names of additional ECS task execution roles (for example per-stage download/snowflake execution roles provisioned by a client overlay) that inject the readiness gate parameters as container secrets. Each is granted ssm:GetParameters on exactly the two gate parameters. The module's own execution role is always granted. Listed roles are attached by name and must already exist when this module is applied; the module neither creates nor depends on them."
  default     = []
}
