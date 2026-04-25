terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(var.tags, { Name = "${var.project_name}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.project_name}-igw" })
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, 0)
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags = merge(var.tags, {
    Name = "${var.project_name}-public-a"
    Tier = "public"
  })
}

resource "aws_subnet" "private_a" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, 1)
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = false
  tags = merge(var.tags, {
    Name = "${var.project_name}-private-a"
    Tier = "private"
  })
}

resource "aws_subnet" "private_b" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, 2)
  availability_zone       = data.aws_availability_zones.available.names[min(1, length(data.aws_availability_zones.available.names) - 1)]
  map_public_ip_on_launch = false
  tags = merge(var.tags, {
    Name = "${var.project_name}-private-b"
    Tier = "private"
  })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.project_name}-public-rt" })
}

resource "aws_route" "public_default" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = merge(var.tags, { Name = "${var.project_name}-nat-eip" })
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public.id
  tags          = merge(var.tags, { Name = "${var.project_name}-nat" })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route_table" "private_a" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.project_name}-private-a-rt" })
}

resource "aws_route_table" "private_b" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.project_name}-private-b-rt" })
}

resource "aws_route" "private_a_default" {
  route_table_id         = aws_route_table.private_a.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.main.id
}

resource "aws_route" "private_b_default" {
  route_table_id         = aws_route_table.private_b.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.main.id
}

resource "aws_route_table_association" "private_a" {
  subnet_id      = aws_subnet.private_a.id
  route_table_id = aws_route_table.private_a.id
}

resource "aws_route_table_association" "private_b" {
  subnet_id      = aws_subnet.private_b.id
  route_table_id = aws_route_table.private_b.id
}

resource "aws_security_group" "ecs_tasks" {
  name        = "${var.project_name}-ecs-tasks"
  description = "Security group for MSSP ECS tasks"
  vpc_id      = aws_vpc.this.id

  egress {
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = merge(var.tags, { Name = "${var.project_name}-ecs-tasks" })
}

resource "aws_ecs_cluster" "this" {
  name = "${var.project_name}-cluster"
  tags = var.tags
}

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${var.project_name}"
  retention_in_days = 30
  tags              = var.tags
}

resource "aws_secretsmanager_secret" "cms_api_key" {
  name = "mssp/cms-api-key"
  tags = var.tags
}

resource "aws_secretsmanager_secret" "cms_api_secret" {
  name = "mssp/cms-api-secret"
  tags = var.tags
}

resource "aws_secretsmanager_secret" "acoms_config" {
  name = "mssp/acoms-config"
  tags = var.tags
}

resource "aws_secretsmanager_secret" "snowflake_rsa_key" {
  count = var.snowflake_rsa_key_secret_name != "" ? 1 : 0
  name  = var.snowflake_rsa_key_secret_name
  tags  = var.tags
}

resource "aws_secretsmanager_secret" "snowflake_rsa_key_passphrase" {
  count = var.snowflake_rsa_key_passphrase_secret_name != "" ? 1 : 0
  name  = var.snowflake_rsa_key_passphrase_secret_name
  tags  = var.tags
}

resource "aws_ssm_parameter" "bootstrap_complete" {
  name  = "/mssp/bootstrap_complete"
  type  = "String"
  value = "false"
  tags  = var.tags

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "whitelist_confirmed" {
  name  = "/mssp/whitelist_confirmed"
  type  = "String"
  value = "false"
  tags  = var.tags

  lifecycle {
    ignore_changes = [value]
  }
}

data "aws_iam_policy_document" "ecs_task_trust" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name               = "${var.project_name}-ecs-task-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_trust.json
  tags               = var.tags
}

resource "aws_iam_role" "bootstrap_task" {
  name               = "${var.project_name}-bootstrap-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_trust.json
  tags               = var.tags
}

resource "aws_iam_role" "runtime_task" {
  name               = "${var.project_name}-runtime-task-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_task_trust.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution_managed" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "ecs_task_execution_secrets" {
  statement {
    sid    = "ReadSecretsForEcsInjection"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = compact(concat(
      [
        aws_secretsmanager_secret.cms_api_key.arn,
        aws_secretsmanager_secret.cms_api_secret.arn,
        aws_secretsmanager_secret.acoms_config.arn,
      ],
      aws_secretsmanager_secret.snowflake_rsa_key[*].arn,
      aws_secretsmanager_secret.snowflake_rsa_key_passphrase[*].arn,
    ))
  }

  statement {
    sid    = "DecryptSecretsIfCustomerManagedKmsUsed"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
    ]
    resources = ["*"]
    # Restrict decrypt to keys used by Secrets Manager in this region. Without
    # this condition any KMS key in the account would be decryptable through
    # the execution role.
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "ecs_task_execution_secrets" {
  name   = "${var.project_name}-ecs-task-execution-secrets"
  role   = aws_iam_role.ecs_task_execution.id
  policy = data.aws_iam_policy_document.ecs_task_execution_secrets.json
}

data "aws_iam_policy_document" "bootstrap_task_policy" {
  statement {
    sid    = "ReadCmsBootstrapInputs"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [
      aws_secretsmanager_secret.cms_api_key.arn,
      aws_secretsmanager_secret.cms_api_secret.arn,
    ]
  }

  statement {
    sid    = "WriteAcomsConfig"
    effect = "Allow"
    actions = [
      "secretsmanager:DescribeSecret",
      "secretsmanager:PutSecretValue",
    ]
    resources = [
      aws_secretsmanager_secret.acoms_config.arn,
    ]
  }

  statement {
    sid    = "SetBootstrapFlags"
    effect = "Allow"
    actions = [
      "ssm:PutParameter",
    ]
    resources = [
      aws_ssm_parameter.bootstrap_complete.arn,
      aws_ssm_parameter.whitelist_confirmed.arn,
    ]
  }
}

resource "aws_iam_role_policy" "bootstrap_task" {
  name   = "${var.project_name}-bootstrap-task-policy"
  role   = aws_iam_role.bootstrap_task.id
  policy = data.aws_iam_policy_document.bootstrap_task_policy.json
}

data "aws_iam_policy_document" "runtime_task_policy" {
  statement {
    sid    = "ReadAcomsConfig"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
    ]
    resources = [
      aws_secretsmanager_secret.acoms_config.arn,
    ]
  }

  statement {
    sid    = "S3DataAccess"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = var.runtime_s3_resource_arns
  }
}

resource "aws_iam_role_policy" "runtime_task" {
  name   = "${var.project_name}-runtime-task-policy"
  role   = aws_iam_role.runtime_task.id
  policy = data.aws_iam_policy_document.runtime_task_policy.json
}

data "aws_iam_policy_document" "events_trust" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "events_invoke_ecs" {
  name               = "${var.project_name}-events-invoke-ecs"
  assume_role_policy = data.aws_iam_policy_document.events_trust.json
  tags               = var.tags
}

data "aws_iam_policy_document" "events_invoke_ecs" {
  statement {
    sid    = "RunEcsTasks"
    effect = "Allow"
    actions = [
      "ecs:RunTask",
    ]
    # Limit to this project's task-definition family. Without scoping, the
    # EventBridge role could RunTask against any task-definition in the account.
    resources = [
      "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${var.project_name}-*",
    ]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.this.arn]
    }
  }

  statement {
    sid    = "PassTaskRolesToEcs"
    effect = "Allow"
    actions = [
      "iam:PassRole",
    ]
    resources = [
      aws_iam_role.runtime_task.arn,
      aws_iam_role.ecs_task_execution.arn,
    ]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "events_invoke_ecs" {
  name   = "${var.project_name}-events-invoke-ecs-policy"
  role   = aws_iam_role.events_invoke_ecs.id
  policy = data.aws_iam_policy_document.events_invoke_ecs.json
}
