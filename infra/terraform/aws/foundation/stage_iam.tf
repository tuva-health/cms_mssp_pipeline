# Least-privilege readiness access. The readiness sidecar (see the ECS task
# definitions) reads the named gate parameters to decide whether a stage may
# run; the task role is granted exactly that read and nothing more.

data "aws_iam_policy_document" "stage_readiness" {
  statement {
    sid     = "ReadReadinessGates"
    effect  = "Allow"
    actions = ["ssm:GetParameter"]
    resources = [
      aws_ssm_parameter.bootstrap_complete.arn,
      aws_ssm_parameter.whitelist_confirmed.arn,
    ]
  }
}

resource "aws_iam_role_policy" "runtime_stage_readiness" {
  name   = "${var.project_name}-runtime-stage-readiness"
  role   = aws_iam_role.runtime_task.id
  policy = data.aws_iam_policy_document.stage_readiness.json
}
