# AWS ECS Container Contract

This document defines how to run the project container for:

- **Runtime scheduled jobs** (read prebuilt `config.txt` from secret)
- **Bootstrap jobs** (generate `config.txt` from CMS API key/secret)

This contract is designed to match:
- `docs/aws-staged-deployment-plan.md`
- `docs/aws-bootstrap-runbook.md`

---

## 1) Runtime scheduled task contract

Container entrypoint: `mssp-entrypoint`

Default command: `mssp-pipeline`

### Required secret/env wiring

Recommended ECS secret mapping:
- `ACOMS_CONFIG_TXT_B64` <- value of `mssp/acoms-config` (base64-encoded string)

Alternative mappings:
- `ACOMS_CONFIG_TXT` <- plain text `config.txt` content
- `ACOMS_CONFIG_PATH` <- path to mounted file containing `config.txt`

Other required env vars depend on your pipeline settings (`MSSP_*`, `AWS_*`, etc).

### Behavior

`mssp-entrypoint`:
1. switches to `MSSP_WORKDIR` (default `/app`)
2. writes `./config.txt` from provided secret env/file
3. enforces presence of `./config.txt` for commands that need it (`mssp-pipeline`, `mssp-download` without `--configure`)
4. executes the requested command

### Entry-point controls

- `MSSP_WORKDIR` (default `/app`)
- `MSSP_WRITE_CONFIG_TXT` (`auto`|`always`|`never`, default `auto`)

---

## 2) Bootstrap task contract

Bootstrap command in ECS task:

```bash
mssp-bootstrap-config
```

### Required env vars

- `CMS_API_KEY`
- `CMS_API_SECRET`

### Optional env vars

- `CMS_WHITELIST_IP` (if configure prompt requires explicit IP entry)
- `MSSP_CLI_PATH` (default `/app/bin/acoms-cli`)
- `MSSP_WORKDIR` (default `/app`)
- `ACOMS_CONFIG_SECRET_ID` (default `mssp/acoms-config`)
- `BOOTSTRAP_COMPLETE_PARAM` (SSM parameter name to set to `true`)
- `WHITELIST_CONFIRMED_PARAM` (SSM parameter name to set to `true`)

### Behavior

`mssp-bootstrap-config`:
1. runs `acoms-cli configure` via `expect` using provided key/secret (and optional IP)
2. verifies `./config.txt` was created and non-empty
3. writes `config.txt` to Secrets Manager via `PutSecretValue`
4. optionally sets bootstrap flag SSM parameters

---

## 3) IAM expectations

### Bootstrap role
- `secretsmanager:GetSecretValue` on key/secret input secrets
- `secretsmanager:PutSecretValue` on `mssp/acoms-config`
- `ssm:PutParameter` on bootstrap gate parameters (if used)

### Runtime role
- `secretsmanager:GetSecretValue` on `mssp/acoms-config`
- no access to CMS key/secret bootstrap inputs

---

## 4) Build notes

Dockerfile expects a Linux-compatible CLI binary under:
- `/app/bin/acoms-cli` (preferred), or
- `/app/bin/acoms-cli-linux` (copied to `/app/bin/acoms-cli` during build)

If your repo currently contains a macOS binary, replace/add the Linux binary before building runtime images.
