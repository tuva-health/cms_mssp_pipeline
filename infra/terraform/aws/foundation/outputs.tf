output "nat_eip_addresses" {
  value = [aws_eip.nat.public_ip]
}

output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_id" {
  value = aws_subnet.public.id
}

output "ecs_subnet_ids" {
  value = [aws_subnet.private_a.id, aws_subnet.private_b.id]
}

output "ecs_security_group_ids" {
  value = [aws_security_group.ecs_tasks.id]
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "ecs_cluster_arn" {
  value = aws_ecs_cluster.this.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.ecs.name
}

output "bootstrap_flags" {
  value = {
    bootstrap_complete  = aws_ssm_parameter.bootstrap_complete.name
    whitelist_confirmed = aws_ssm_parameter.whitelist_confirmed.name
  }
}

output "ecs_task_execution_role_arn" {
  value = aws_iam_role.ecs_task_execution.arn
}

output "bootstrap_task_role_arn" {
  value = aws_iam_role.bootstrap_task.arn
}

output "runtime_task_role_arn" {
  value = aws_iam_role.runtime_task.arn
}

output "events_invoke_role_arn" {
  value = aws_iam_role.events_invoke_ecs.arn
}

output "snowflake_rsa_key_secret_arn" {
  value = try(aws_secretsmanager_secret.snowflake_rsa_key[0].arn, null)
}

output "snowflake_rsa_key_passphrase_secret_arn" {
  value = try(aws_secretsmanager_secret.snowflake_rsa_key_passphrase[0].arn, null)
}

output "data_bucket_name" {
  value = try(aws_s3_bucket.data[0].id, null)
}

output "pipeline_repository_url" {
  value = try(aws_ecr_repository.pipeline[0].repository_url, null)
}

output "connector_repository_url" {
  value = try(aws_ecr_repository.connector[0].repository_url, null)
}
