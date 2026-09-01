variable "table_name" {
  type        = string
  description = "Name of the DynamoDB lock table. Supplied by the overlay; never a literal in this module."

  validation {
    condition     = length(trimspace(var.table_name)) > 0
    error_message = "table_name must be a non-empty DynamoDB table name."
  }
}

variable "region" {
  type        = string
  description = "AWS region the lock table is provisioned in. The caller must configure its aws provider for this same region; the value is echoed back as an output so the overlay can wire it into the DynamoDbLeaseBackend."

  validation {
    condition     = length(trimspace(var.region)) > 0
    error_message = "region must be a non-empty AWS region, e.g. us-east-1."
  }
}

variable "hash_key" {
  type        = string
  description = "Partition key attribute name for the lock table (one item per lease)."
  default     = "lease_name"
}

variable "ttl_attribute_name" {
  type        = string
  description = "Numeric attribute holding the lease expiry epoch seconds, used as the table's DynamoDB TTL attribute. Matches DynamoDbLeaseBackend's expires_at."
  default     = "expires_at"
}

variable "point_in_time_recovery" {
  type        = bool
  description = "Enable point-in-time recovery. A lock table is ephemeral, so this defaults off; overlays may enable it."
  default     = false
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to the lock table."
  default     = {}
}
