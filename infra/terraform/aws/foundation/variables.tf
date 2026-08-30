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
