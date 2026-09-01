terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

# Generic, client-neutral distributed-lock table for the sequencer's
# DynamoDbLeaseBackend. One item per lease, keyed by lease_name; the adapter
# holds owner / acquired_at / expires_at / fencing_token and decides expiry
# logically via a conditional write. DynamoDB's native TTL only sweeps expired
# rows opportunistically and is never relied on for lock correctness.
resource "aws_dynamodb_table" "lease" {
  name         = var.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = var.hash_key

  attribute {
    name = var.hash_key
    type = "S"
  }

  ttl {
    attribute_name = var.ttl_attribute_name
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.point_in_time_recovery
  }

  tags = merge(var.tags, { Name = var.table_name })
}
