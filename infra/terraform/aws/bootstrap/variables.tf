variable "aws_account_id" {
  type        = string
  description = "AWS account that owns the MSSP backend and deployment role."

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be a 12-digit AWS account id."
  }
}

variable "aws_region" {
  type        = string
  description = "AWS region for the state bucket and deployment role."

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must be an AWS region code, for example us-east-1."
  }
}

variable "state_bucket_name" {
  type        = string
  description = "Globally unique name of the dedicated Terraform state bucket."

  validation {
    condition     = length(var.state_bucket_name) >= 3 && length(var.state_bucket_name) <= 63
    error_message = "state_bucket_name must be a valid S3 bucket name between 3 and 63 characters."
  }
}

variable "deployer_principal_arns" {
  type        = list(string)
  description = "IAM or IAM Identity Center role ARNs for named operators allowed to assume the deployment role."

  validation {
    condition = length(var.deployer_principal_arns) > 0 && alltrue([
      for arn in var.deployer_principal_arns : can(regex("^arn:aws:iam::[0-9]{12}:role/.+", arn))
    ])
    error_message = "Provide at least one IAM role ARN; user ARNs and static access-key identities are not accepted."
  }
}

variable "deployer_role_name" {
  type        = string
  description = "Name of the non-personal deployment role."
  default     = "mssp-deployer"
}

variable "deployer_max_session_duration" {
  type        = number
  description = "Maximum assumed-role session duration in seconds."
  default     = 14400
}

variable "deployer_managed_policy_arns" {
  type        = list(string)
  description = "Managed policy ARNs attached to the deployment role."
  default     = ["arn:aws:iam::aws:policy/AdministratorAccess"]
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to bootstrap resources."
  default = {
    ManagedBy = "Terraform"
  }
}
