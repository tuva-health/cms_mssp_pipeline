output "table_name" {
  description = "Name of the provisioned lock table (feed to DynamoDbLeaseBackend's table_name)."
  value       = aws_dynamodb_table.lease.name
}

output "table_arn" {
  description = "ARN of the provisioned lock table, for IAM policy scoping."
  value       = aws_dynamodb_table.lease.arn
}

output "table_id" {
  description = "DynamoDB table id."
  value       = aws_dynamodb_table.lease.id
}

output "region" {
  description = "AWS region the lock table lives in (feed to DynamoDbLeaseBackend's region)."
  value       = var.region
}

output "hash_key" {
  description = "Partition key attribute name of the lock table."
  value       = var.hash_key
}

output "ttl_attribute_name" {
  description = "TTL / expiry attribute name of the lock table."
  value       = var.ttl_attribute_name
}
