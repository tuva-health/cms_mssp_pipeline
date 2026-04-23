terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.0"
    }
  }
}

provider "aws" {
  region = var.region
}

data "terraform_remote_state" "foundation_local" {
  count   = var.foundation_state_backend == "local" ? 1 : 0
  backend = "local"
  config = {
    path = var.foundation_state_local_path
  }
}

data "terraform_remote_state" "foundation_s3" {
  count   = var.foundation_state_backend == "s3" ? 1 : 0
  backend = "s3"
  config = {
    bucket         = var.foundation_state_s3_bucket
    key            = var.foundation_state_s3_key
    region         = var.foundation_state_s3_region
    dynamodb_table = var.foundation_state_s3_dynamodb_table
  }
}

locals {
  foundation_outputs = var.foundation_state_backend == "s3" ? data.terraform_remote_state.foundation_s3[0].outputs : data.terraform_remote_state.foundation_local[0].outputs

  effective_ecs_cluster_arn = var.ecs_cluster_arn != "" ? var.ecs_cluster_arn : try(local.foundation_outputs.ecs_cluster_arn, "")
  effective_ecs_subnet_ids  = length(var.ecs_subnet_ids) > 0 ? var.ecs_subnet_ids : try(local.foundation_outputs.ecs_subnet_ids, [])
  effective_ecs_sg_ids      = length(var.ecs_security_group_ids) > 0 ? var.ecs_security_group_ids : try(local.foundation_outputs.ecs_security_group_ids, [])
  effective_events_role_arn = var.events_invoke_role_arn != "" ? var.events_invoke_role_arn : try(local.foundation_outputs.events_invoke_role_arn, "")
}

data "aws_ssm_parameter" "bootstrap_complete" {
  name = "/mssp/bootstrap_complete"
}

data "aws_ssm_parameter" "whitelist_confirmed" {
  name = "/mssp/whitelist_confirmed"
}

data "aws_secretsmanager_secret" "acoms_config" {
  name = "mssp/acoms-config"
}

data "aws_secretsmanager_secret_version" "acoms_config_current" {
  secret_id = data.aws_secretsmanager_secret.acoms_config.id
}

resource "null_resource" "activation_gates" {
  lifecycle {
    precondition {
      condition     = trimspace(data.aws_ssm_parameter.bootstrap_complete.value) == "true"
      error_message = "Activation blocked: /mssp/bootstrap_complete must be true"
    }
    precondition {
      condition     = trimspace(data.aws_ssm_parameter.whitelist_confirmed.value) == "true"
      error_message = "Activation blocked: /mssp/whitelist_confirmed must be true"
    }
    precondition {
      condition     = length(trimspace(data.aws_secretsmanager_secret_version.acoms_config_current.secret_string)) > 0
      error_message = "Activation blocked: mssp/acoms-config is empty"
    }
    precondition {
      condition     = local.effective_ecs_cluster_arn != ""
      error_message = "Activation blocked: ecs cluster ARN unavailable (set var.ecs_cluster_arn or provide foundation remote state)"
    }
    precondition {
      condition     = length(local.effective_ecs_subnet_ids) > 0
      error_message = "Activation blocked: ecs subnet ids unavailable (set var.ecs_subnet_ids or provide foundation remote state)"
    }
    precondition {
      condition     = length(local.effective_ecs_sg_ids) > 0
      error_message = "Activation blocked: ecs security group ids unavailable (set var.ecs_security_group_ids or provide foundation remote state)"
    }
    precondition {
      condition     = local.effective_events_role_arn != ""
      error_message = "Activation blocked: events invoke role ARN unavailable (set var.events_invoke_role_arn or provide foundation remote state)"
    }
  }
}

resource "aws_cloudwatch_event_rule" "mssp_schedule" {
  name                = "${var.project_name}-schedule"
  description         = "Runs mssp runtime ECS task on schedule"
  schedule_expression = var.schedule_expression
  state               = var.enable_schedule ? "ENABLED" : "DISABLED"
}

resource "aws_cloudwatch_event_rule" "process_schedule" {
  count               = var.enable_process_schedule ? 1 : 0
  name                = "${var.project_name}-process-schedule"
  description         = "Runs mssp process-only ECS task on schedule"
  schedule_expression = var.process_schedule_expression
  state               = var.enable_process_schedule ? "ENABLED" : "DISABLED"
}

resource "aws_cloudwatch_event_target" "ecs" {
  depends_on = [null_resource.activation_gates]

  rule      = aws_cloudwatch_event_rule.mssp_schedule.name
  target_id = "mssp-runtime"
  arn       = local.effective_ecs_cluster_arn
  role_arn  = local.effective_events_role_arn

  ecs_target {
    task_definition_arn = var.runtime_task_definition_arn
    task_count          = 1
    launch_type         = "FARGATE"
    network_configuration {
      subnets          = local.effective_ecs_subnet_ids
      security_groups  = local.effective_ecs_sg_ids
      assign_public_ip = false
    }
  }
}

resource "aws_cloudwatch_event_target" "process_ecs" {
  count      = var.enable_process_schedule ? 1 : 0
  depends_on = [null_resource.activation_gates]

  rule      = aws_cloudwatch_event_rule.process_schedule[0].name
  target_id = "mssp-process-runtime"
  arn       = local.effective_ecs_cluster_arn
  role_arn  = local.effective_events_role_arn
  input = jsonencode({
    containerOverrides = [
      {
        name    = "mssp-runtime"
        command = ["mssp-process"]
        environment = [
          {
            name  = "SNOWFLAKE_DATABASE"
            value = var.process_database
          },
          {
            name  = "SNOWFLAKE_SCHEMA"
            value = var.process_schema
          },
          {
            name  = "MSSP_FULL_REFRESH"
            value = "false"
          }
        ]
      }
    ]
  })

  ecs_target {
    task_definition_arn = var.runtime_task_definition_arn
    task_count          = 1
    launch_type         = "FARGATE"
    network_configuration {
      subnets          = local.effective_ecs_subnet_ids
      security_groups  = local.effective_ecs_sg_ids
      assign_public_ip = false
    }
  }
}
