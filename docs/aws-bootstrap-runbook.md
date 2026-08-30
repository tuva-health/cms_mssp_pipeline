# AWS Bootstrap Runbook (CMS whitelist + `config.txt` generation)

Use this runbook after Foundation deployment and before enabling scheduled production runs.

---

## 0) Inputs and prerequisites

Required:
- Foundation stack deployed
- NAT EIP(s) available from IaC outputs
- Secrets exist in Secrets Manager:
  - `mssp/cms-api-key`
  - `mssp/cms-api-secret`
- Bootstrap task definition available (one-off task)

Do not proceed until:
- CMS confirms whitelist includes all NAT EIP(s) used by bootstrap/runtime egress

---

## 1) Confirm whitelist + gates

1. Verify NAT EIP output from IaC:
   - `nat_eip_addresses`
2. Confirm those same IPs were submitted and approved by CMS.
3. Set SSM parameter if your process uses explicit confirmation:
   - `/mssp/whitelist_confirmed = true`

If not confirmed, stop here.

---

## 2) Validate bootstrap input secrets

Check both secrets are present and non-empty:
- `mssp/cms-api-key`
- `mssp/cms-api-secret`

If missing/empty, create/update them before continuing.

---

## 3) Run bootstrap task

Run a one-off bootstrap task in ECS (same VPC/subnets/security groups/NAT path as runtime).

Bootstrap task responsibilities:
1. Read `mssp/cms-api-key` and `mssp/cms-api-secret`
2. Execute `mssp-download --configure` using scripted stdin/expect
3. Ensure `./config.txt` is generated in task working directory
4. Upload `./config.txt` content to secret `mssp/acoms-config`

Expected result:
- `mssp/acoms-config` gets a new secret version

---

## 4) Post-bootstrap verification

Verify:
- `mssp/acoms-config` exists and is non-empty
- secret version timestamp is current
- bootstrap task logs show successful configure + secret write

Then set:
- `/mssp/bootstrap_complete = true`

---

## 5) Activate runtime schedule

Only after gates pass:
- `/mssp/bootstrap_complete == true`
- `/mssp/whitelist_confirmed == true`
- `mssp/acoms-config` non-empty

Then:
1. Enable EventBridge schedule
2. Trigger one manual smoke run
3. Confirm logs and output state are healthy

Example manual runtime smoke test:

```bash
AWS_PROFILE=<profile> AWS_REGION=<region> aws ecs run-task \
  --cluster <ecs-cluster-name> \
  --launch-type FARGATE \
  --task-definition mssp-pipeline-runtime \
  --network-configuration 'awsvpcConfiguration={subnets=[subnet-aaa,subnet-bbb],securityGroups=[sg-ccc],assignPublicIp=DISABLED}' \
  --count 1 \
  --query 'tasks[0].taskArn' \
  --output text

AWS_PROFILE=<profile> AWS_REGION=<region> aws logs tail /ecs/mssp-pipeline --follow
```

---

## 6) Rotation runbook (repeatable)

When CMS credentials change:
1. Update `mssp/cms-api-key` / `mssp/cms-api-secret`
2. Re-run this bootstrap runbook
3. Verify new `mssp/acoms-config` version
4. Redeploy or force new task run

---

## 7) Rollback

If new bootstrap output is invalid:
1. Disable schedule
2. Revert `mssp/acoms-config` to prior known-good version
3. Re-run smoke task
4. Re-enable schedule only after success
