output "schedule_rule_name" {
  value = aws_cloudwatch_event_rule.mssp_schedule.name
}

output "schedule_state" {
  value = aws_cloudwatch_event_rule.mssp_schedule.state
}

output "effective_ecs_cluster_arn" {
  value = local.effective_ecs_cluster_arn
}

output "effective_ecs_subnet_ids" {
  value = local.effective_ecs_subnet_ids
}

output "effective_ecs_security_group_ids" {
  value = local.effective_ecs_sg_ids
}

output "effective_events_invoke_role_arn" {
  value = local.effective_events_role_arn
}
