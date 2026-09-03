# Terraform skeleton for AWS staged deployment

This skeleton matches the staged rollout model:

1. `foundation/` — creates default network + bootstrap prerequisites
2. `activate/` — gated schedule enablement after bootstrap

## Foundation

Creates:
- VPC (`10.42.0.0/16` by default)
- 1 public subnet + 2 private subnets
- Internet Gateway + NAT Gateway + Elastic IP (for CMS whitelist)
- Route tables/associations (private subnets egress through NAT)
- ECS cluster
- ECS task security group (egress-all)
- IAM roles/policies for:
  - ECS task execution
  - bootstrap task
  - runtime task
  - EventBridge -> ECS task invocation
- CloudWatch log group
- Secrets placeholders (`mssp/cms-api-key`, `mssp/cms-api-secret`, `mssp/acoms-config`)
- Optional backend secret placeholders such as Snowflake RSA key/passphrase secrets when configured in `foundation.tfvars`
- SSM gates set to false

Example:

```bash
cd infra/terraform/aws/foundation
terraform init
terraform apply -var region=us-east-1
```

Capture output `nat_eip_addresses` and submit to CMS whitelist.

### Useful outputs from foundation

- `ecs_cluster_arn`
- `ecs_subnet_ids`
- `ecs_security_group_ids`
- `events_invoke_role_arn`
- task role ARNs (execution/bootstrap/runtime)
- optional Snowflake secret ARNs if foundation created them

These are consumed by `activate` and task-definition rendering/registration.

## Activate

Checks gates:
- `/mssp/bootstrap_complete == true`
- `/mssp/whitelist_confirmed == true`
- `mssp/acoms-config` current value is non-empty

Then creates/enables schedule target for ECS task.

By default, `activate` reads network/ECS placement values from foundation state:
- local state: `foundation_state_backend = "local"`
- s3 state: `foundation_state_backend = "s3"` (+ bucket/key/region vars)

Example (local state):

```bash
cd infra/terraform/aws/activate
terraform init
terraform apply \
  -var region=us-east-1 \
  -var schedule_expression='cron(0 6 * * ? *)' \
  -var runtime_task_definition_arn=arn:aws:ecs:...:task-definition/mssp-pipeline-runtime:1
```

If you use `scripts/deploy-client.sh <client> activate`, the wrapper passes the exact `mssp-pipeline-runtime` revision recorded by `register-taskdefs` (in `<overlay>/rendered/task-definition-arns.json`) to Terraform, so you do not need to update `runtime_task_definition_arn` manually after each deploy.

You can still override `events_invoke_role_arn`, `ecs_cluster_arn`, `ecs_subnet_ids`, and `ecs_security_group_ids` manually if needed.

## Register task definitions

Use AWS CLI with rendered templates in `infra/clients/<client>/rendered/` via:

```bash
scripts/deploy-client.sh <client> render-taskdefs
scripts/deploy-client.sh <client> register-taskdefs
scripts/deploy-client.sh <client> activate
```

Typical image rollout:

```bash
RELEASE_ID=2026-04-15-oomfix
scripts/build-and-push-image.sh <client> "$RELEASE_ID"
export PIPELINE_IMAGE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image"])' "release-metadata/$RELEASE_ID.json")"
scripts/deploy-client.sh <client> render-taskdefs
scripts/deploy-client.sh <client> register-taskdefs
scripts/deploy-client.sh <client> activate
```

`build-and-push-image.sh` derives Docker `PIP_EXTRAS` from `MSSP_OUTPUT_TYPE` in the client env (for example `processing,snowflake` for Snowflake). Override explicitly with `PIP_EXTRAS=...` if needed.

Or register directly with AWS CLI after replacing placeholders in templates under `infra/aws/ecs/`.
