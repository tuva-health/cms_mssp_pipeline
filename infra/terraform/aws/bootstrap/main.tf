# Remote-state backend bootstrap.
#
# Creates the dedicated, hardened S3 bucket that holds Terraform state for the
# foundation and activate roots, plus a non-personal deployment role. State
# locking uses S3 native locking (use_lockfile) -- no DynamoDB table.
#
# This is a generic engine: account, region, bucket name, deployer principals,
# role name, and tags are all inputs. No client identity is embedded here.

terraform {
  required_version = ">= 1.14.0"

  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0"
    }
  }
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
}

data "aws_iam_policy_document" "state_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.terraform_state.arn,
      "${aws_s3_bucket.terraform_state.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket" "terraform_state" {
  bucket = var.state_bucket_name
  tags   = merge(var.tags, { Name = var.state_bucket_name })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_policy" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  policy = data.aws_iam_policy_document.state_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.terraform_state]
}

data "aws_iam_policy_document" "deployer_trust" {
  statement {
    sid     = "NamedOperatorsOnly"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = var.deployer_principal_arns
    }
  }
}

resource "aws_iam_role" "deployer" {
  name                 = var.deployer_role_name
  description          = "Non-personal deployment and recovery role for the MSSP platform."
  assume_role_policy   = data.aws_iam_policy_document.deployer_trust.json
  max_session_duration = var.deployer_max_session_duration
  tags                 = var.tags
}

# Initial scope is broad (infrastructure administration and secret
# retrieval/rotation). Override deployer_managed_policy_arns to tighten to least
# privilege once the concrete operations are known.
resource "aws_iam_role_policy_attachment" "deployer_managed" {
  for_each   = toset(var.deployer_managed_policy_arns)
  role       = aws_iam_role.deployer.name
  policy_arn = each.value
}
