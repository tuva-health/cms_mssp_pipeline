# Immutable, scan-on-push ECR repositories for the pipeline and connector
# images. Immutable tags are what let a release id resolve to exactly one
# digest (see scripts/build-and-push-image.sh). Repository names are inputs.

resource "aws_ecr_repository" "pipeline" {
  count                = var.create_ecr_repositories ? 1 : 0
  name                 = var.pipeline_repository_name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = var.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_ecr_repository" "connector" {
  count                = var.create_ecr_repositories ? 1 : 0
  name                 = var.connector_repository_name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = var.tags

  lifecycle {
    prevent_destroy = true
  }
}
