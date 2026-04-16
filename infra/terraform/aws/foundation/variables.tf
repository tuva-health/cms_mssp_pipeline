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
  description = "S3 ARNs runtime task role can access (List/Get/Put/Delete)."
  default     = ["arn:aws:s3:::*", "arn:aws:s3:::*/*"]
}
