# Least-privilege readiness access. The readiness sidecar (see the ECS task
# definitions) reads the named gate parameters to decide whether a stage may
# run; the task role is granted exactly that read and nothing more.

locals {
  readiness_gate_parameter_arns = [
    aws_ssm_parameter.bootstrap_complete.arn,
    aws_ssm_parameter.whitelist_confirmed.arn,
  ]
}

data "aws_iam_policy_document" "stage_readiness" {
  statement {
    sid       = "ReadReadinessGates"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = local.readiness_gate_parameter_arns
  }
}

resource "aws_iam_role_policy" "runtime_stage_readiness" {
  name   = "${var.project_name}-runtime-stage-readiness"
  role   = aws_iam_role.runtime_task.id
  policy = data.aws_iam_policy_document.stage_readiness.json
}

# The gate values reach the sidecar as ECS container secrets (`valueFrom` = the
# SSM parameter ARN, rendered from the <READINESS_<GATE>_PARAM_ARN>
# placeholders). ECS resolves container secrets with the task EXECUTION role at
# task start, so each execution role needs ssm:GetParameters on exactly the two
# gate parameters -- nothing else. The parameters are plain String type, so no
# KMS grant is involved.

data "aws_iam_policy_document" "stage_readiness_injection" {
  statement {
    sid       = "InjectReadinessGates"
    effect    = "Allow"
    actions   = ["ssm:GetParameters"]
    resources = local.readiness_gate_parameter_arns
  }
}

resource "aws_iam_role_policy" "ecs_task_execution_readiness" {
  name   = "${var.project_name}-ecs-task-execution-readiness"
  role   = aws_iam_role.ecs_task_execution.id
  policy = data.aws_iam_policy_document.stage_readiness_injection.json
}

# Per-stage execution roles (download / snowflake / ...) that a client overlay
# provisions outside this module are attached by name. The inline policy is
# named "<role>-readiness" so one created out-of-band with the same name can be
# imported here later: aws_iam_role_policy ids are "<role>:<policy>".
resource "aws_iam_role_policy" "stage_execution_readiness" {
  for_each = toset(var.readiness_execution_role_names)

  name   = "${each.value}-readiness"
  role   = each.value
  policy = data.aws_iam_policy_document.stage_readiness_injection.json
}
