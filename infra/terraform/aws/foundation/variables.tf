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
  description = "S3 ARNs the runtime task role can access (List/Get/Put). Scope this to the file store; the default is deliberately broad and should be overridden per deployment."
  default     = ["arn:aws:s3:::*", "arn:aws:s3:::*/*"]
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
