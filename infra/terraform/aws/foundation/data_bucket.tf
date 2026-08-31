# Optional managed ACO data bucket. When var.data_bucket_name is set, the bucket
# is hardened (versioned, encrypted, private, TLS-only) and the runtime task
# role is granted least-privilege access to it. The bucket name is a client
# input; nothing here is client-specific.

locals {
  manage_data_bucket = var.data_bucket_name != ""
}

resource "aws_s3_bucket" "data" {
  count  = local.manage_data_bucket ? 1 : 0
  bucket = var.data_bucket_name
  tags   = merge(var.tags, { Name = var.data_bucket_name })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "data" {
  count  = local.manage_data_bucket ? 1 : 0
  bucket = aws_s3_bucket.data[0].id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  count  = local.manage_data_bucket ? 1 : 0
  bucket = aws_s3_bucket.data[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  count  = local.manage_data_bucket ? 1 : 0
  bucket = aws_s3_bucket.data[0].id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  count  = local.manage_data_bucket ? 1 : 0
  bucket = aws_s3_bucket.data[0].id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

data "aws_iam_policy_document" "data_bucket" {
  count = local.manage_data_bucket ? 1 : 0
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.data[0].arn,
      "${aws_s3_bucket.data[0].arn}/*",
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

resource "aws_s3_bucket_policy" "data" {
  count      = local.manage_data_bucket ? 1 : 0
  bucket     = aws_s3_bucket.data[0].id
  policy     = data.aws_iam_policy_document.data_bucket[0].json
  depends_on = [aws_s3_bucket_public_access_block.data]
}
