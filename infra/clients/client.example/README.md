# Client overlay example (do not store real client data in git)

Copy this directory for each client deployment and keep real values out of source control.

Example:

```bash
cp -R infra/clients/client.example infra/clients/acme
cp infra/clients/acme/env.sh.example infra/clients/acme/env.sh
cp infra/clients/acme/foundation.tfvars.example infra/clients/acme/foundation.tfvars
cp infra/clients/acme/activate.tfvars.example infra/clients/acme/activate.tfvars
```

Then fill in values and run:

```bash
scripts/check-client-config.sh acme --for all
scripts/deploy-client.sh acme all
scripts/deploy-and-smoke-client.sh acme 2026-04-17 -- mssp-validate --target process --strict
```

## Files

- `env.sh` (local only): AWS profile/region + render vars used for ECS task definitions (`FILE_STORE_PREFIX` and `OUTPUT_PREFIX` can be empty for bucket root); also holds backend-specific runtime settings such as Snowflake env values and secret IDs
- `foundation.tfvars`: inputs for `infra/terraform/aws/foundation`
- `activate.tfvars`: inputs for `infra/terraform/aws/activate` (defaults to reading foundation local state)
- `foundation.backend.hcl` / `activate.backend.hcl` (optional): backend config per stage for remote Terraform state
