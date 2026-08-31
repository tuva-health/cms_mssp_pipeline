output "state_bucket_name" {
  description = "Dedicated Terraform state bucket."
  value       = aws_s3_bucket.terraform_state.id
}

output "deployer_role_arn" {
  description = "Role assumed by named MSSP deployment operators."
  value       = aws_iam_role.deployer.arn
}
