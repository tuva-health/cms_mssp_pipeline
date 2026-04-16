# AWS Staged Deployment Plan (Option 1: Runtime uses prebuilt `config.txt` secret)

This document defines a production deployment flow for `mssp_pipeline` in AWS where:

- CMS API credentials (`key` + `secret`) are stored as bootstrap input secrets.
- `acoms-cli config.txt` is generated only during controlled bootstrap/rotation.
- Scheduled runtime tasks read only the generated `config.txt` secret.

---

## 1) Architecture assumptions

- Compute: ECS Fargate scheduled task (via EventBridge Scheduler/Rule)
- Networking: private subnets with NAT Gateway egress
- Static outbound IP for CMS whitelist: NAT Elastic IP (EIP)
- Secret store: AWS Secrets Manager
- Logs: CloudWatch Logs
- Data store: S3 (`MSSP_FILE_STORE=s3://...`) or other supported store

---

## 2) Secret model (required)

### Bootstrap input secrets
- `mssp/cms-api-key`
- `mssp/cms-api-secret`

### Runtime secret artifact
- `mssp/acoms-config` (the full `config.txt` content)

### Why this model
- Runtime job stays simple and stable.
- API key/secret are not needed by scheduled runtime task.
- Rotation is explicit/auditable: regenerate `config.txt` in bootstrap only.

---

## 3) Deployment stages

## Stage A — Foundation (fully automated IaC)

Provision:
- VPC + private subnets
- NAT Gateway + EIP(s)
- ECS cluster + task execution role + runtime task role
- CloudWatch log group
- Secrets placeholders:
  - `mssp/cms-api-key` (empty or seeded)
  - `mssp/cms-api-secret` (empty or seeded)
  - `mssp/acoms-config` (empty placeholder)
- SSM parameters:
  - `/mssp/bootstrap_complete = false`
  - `/mssp/whitelist_confirmed = false`
- EventBridge schedule created but **disabled**

IaC outputs:
- `nat_eip_addresses` (must be sent to CMS whitelist process)

## Stage B — Bootstrap (manual but scripted)

Preconditions:
- CMS has confirmed whitelist for all NAT EIP(s)
- Input secrets exist: `mssp/cms-api-key`, `mssp/cms-api-secret`

Actions:
1. Launch one-off bootstrap task in the same VPC/subnets/NAT path as runtime.
2. Bootstrap task reads key+secret from Secrets Manager.
3. Bootstrap task runs `mssp-download --configure` non-interactively (scripted input).
4. Bootstrap task validates that `config.txt` was created.
5. Bootstrap task writes `config.txt` content to `mssp/acoms-config`.
6. Set SSM flags:
   - `/mssp/bootstrap_complete = true`
   - `/mssp/whitelist_confirmed = true`

## Stage C — Activate (automated and gated)

CI/CD gate checks (must all pass):
- `/mssp/bootstrap_complete == true`
- `/mssp/whitelist_confirmed == true`
- `mssp/acoms-config` exists and is non-empty

Then:
- Register/update runtime task definition
- Enable EventBridge schedule
- Run one smoke task (`mssp-validate --target pipeline --strict --live`)

---

## 4) IAM boundary model

## Bootstrap task role
Allow:
- `secretsmanager:GetSecretValue` on:
  - `mssp/cms-api-key`
  - `mssp/cms-api-secret`
- `secretsmanager:PutSecretValue` on:
  - `mssp/acoms-config`
- `ssm:PutParameter` on bootstrap flag parameters
- Minimal CloudWatch Logs permissions

## Runtime task role
Allow:
- `secretsmanager:GetSecretValue` on:
  - `mssp/acoms-config`
- No access to `mssp/cms-api-key` / `mssp/cms-api-secret`
- Minimal S3/object store access required for pipeline
- Minimal CloudWatch Logs permissions

---

## 5) Runtime contract (for later Docker implementation)

Scheduled runtime task startup must:
1. Read `mssp/acoms-config` from Secrets Manager
2. Write it to `./config.txt` in container working directory
3. Run `mssp-pipeline ...` from that same working directory
4. Optionally delete `./config.txt` on process exit

This is required because `acoms-cli` expects `config.txt` in current working directory.

---

## 6) Rotation procedure

When CMS credentials rotate:
1. Update `mssp/cms-api-key` and/or `mssp/cms-api-secret`
2. Re-run Stage B bootstrap to regenerate `config.txt`
3. Confirm new `mssp/acoms-config` secret version exists
4. Force new runtime task deployment (or let next scheduled run pick up latest secret)

---

## 7) Failure handling

- If bootstrap fails, keep schedule disabled.
- If whitelist changes (new NAT EIP), reset `/mssp/whitelist_confirmed=false` until CMS confirms.
- Runtime failures should alarm through CloudWatch + SNS/Slack.
