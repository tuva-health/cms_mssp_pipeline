# AWS IAM Minimum Policies (Bootstrap + Runtime)

These are baseline policy templates. Scope ARNs to your account/region/resources before use.

---

## 1) Bootstrap task role policy

Use for one-off `mssp-bootstrap-config` runs.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadBootstrapInputSecrets",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": [
        "arn:aws:secretsmanager:<REGION>:<ACCOUNT_ID>:secret:mssp/cms-api-key*",
        "arn:aws:secretsmanager:<REGION>:<ACCOUNT_ID>:secret:mssp/cms-api-secret*"
      ]
    },
    {
      "Sid": "WriteDerivedAcomsConfigSecret",
      "Effect": "Allow",
      "Action": ["secretsmanager:PutSecretValue", "secretsmanager:DescribeSecret"],
      "Resource": "arn:aws:secretsmanager:<REGION>:<ACCOUNT_ID>:secret:mssp/acoms-config*"
    },
    {
      "Sid": "WriteBootstrapFlags",
      "Effect": "Allow",
      "Action": ["ssm:PutParameter"],
      "Resource": [
        "arn:aws:ssm:<REGION>:<ACCOUNT_ID>:parameter/mssp/bootstrap_complete",
        "arn:aws:ssm:<REGION>:<ACCOUNT_ID>:parameter/mssp/whitelist_confirmed"
      ]
    }
  ]
}
```

If your secret uses a customer KMS key, add `kms:Decrypt/Encrypt` as needed.

---

## 2) Runtime task role policy

Use for scheduled `mssp-pipeline` runs.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadAcomsConfigOnly",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:<REGION>:<ACCOUNT_ID>:secret:mssp/acoms-config*"
    },
    {
      "Sid": "S3DataAccess",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": [
        "arn:aws:s3:::<MSSP_BUCKET>",
        "arn:aws:s3:::<MSSP_BUCKET>/*"
      ]
    }
  ]
}
```

Do **not** grant runtime access to `mssp/cms-api-key` or `mssp/cms-api-secret`.

---

## 3) ECS task execution role (managed baseline)

Attach AWS managed policy:
- `service-role/AmazonECSTaskExecutionRolePolicy`

This covers image pulls from ECR and log writes to CloudWatch.
