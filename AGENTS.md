# AGENTS.md

## Project Overview

Two subsystems: **Integration** (download via `acoms-cli`) and **Processing** (ETL via DuckDB to 8 backends).

Package entry points:
- `mssp_pipeline/integration/` — download subsystem
- `mssp_pipeline/processing/` — ETL subsystem

## Setup

```bash
cp .env.example .env
uv sync --group dev
uv run mssp-download --configure  # creates config.txt
```

## Configuration

**Primary config method: `.env`** (loaded by `mssp_pipeline/config.py`). Edit `.env`, not Python config.

| Variable | Purpose |
|---|---|
| `MSSP_ACO_ID` | ACO identifier (e.g. `A1234`) |
| `MSSP_FILE_STORE` | Source/output directory (local, `s3://`, `az://`, `gs://`) |
| `MSSP_OUTPUT_TYPE` | Backend: `PARQUET`, `DUCKDB`, `MOTHERDUCK`, `SNOWFLAKE`, `DATABRICKS`, `BIGQUERY`, `REDSHIFT`, `FABRIC` |
| `MSSP_START_YEAR` | First performance year for downloads |

Full list in `.env.example`.

## Running

```bash
uv run mssp-download --aco=C1234 --start-year=2020 --mode=incremental
uv run mssp-process
uv run mssp-pipeline --aco=C1234 --start-year=2020 --mode=incremental
uv run mssp-validate --target pipeline --strict  # pre-flight checks
uv run mssp-runs --limit 10                      # inspect run manifests
```

## AWS / ECS / ECR deployment

Preferred deploy flow:

```bash
scripts/build-and-push-image.sh <client> <release-id>   # writes release-metadata/<release-id>.json
export PIPELINE_IMAGE=<repository@sha256 digest from that metadata>
scripts/deploy-client.sh <client> render-taskdefs
scripts/deploy-client.sh <client> register-taskdefs
scripts/deploy-client.sh <client> activate
```

One-command deploy + smoke test:

```bash
scripts/deploy-and-smoke-client.sh <client> <release-id>
scripts/deploy-and-smoke-client.sh <client> <release-id> -- mssp-validate --target process --strict
```

One-command process-only ECS run:

```bash
scripts/run-client-process-task.sh <client> --database <db>
scripts/run-client-process-task.sh <client> --database <db> --schema RAW_DATA
```

Conventions:
- `scripts/build-and-push-image.sh` derives Docker `PIP_EXTRAS` from `MSSP_OUTPUT_TYPE` (e.g. `processing,snowflake` for Snowflake).
- Images are immutable: `scripts/build-and-push-image.sh` pushes an immutable release tag and records the `repository@sha256` digest in `release-metadata/<release-id>.json`; `scripts/deploy-client.sh render-taskdefs` requires that digest as `PIPELINE_IMAGE` (no mutable `:tag`).
- Revisions are exact: `register-taskdefs` records each registered task-definition ARN in `<overlay>/rendered/task-definition-arns.json`, and `activate` binds the recorded `mssp-pipeline-runtime` revision (never a "latest" family lookup).
- `scripts/deploy-and-smoke-client.sh` wraps build/push + render/register/activate + a one-off `aws ecs run-task` smoke execution against the recorded runtime revision; the built release's digest is handed to the deploy as `PIPELINE_IMAGE`.
- `scripts/run-client-process-task.sh` runs a one-off ECS task against the recorded runtime revision with command override `mssp-process` and destination overrides such as `SNOWFLAKE_DATABASE` / `SNOWFLAKE_SCHEMA`.
- Both runners accept `--skip-build` (digest from `release-metadata/<release-id>.json` when a release id is given, else `PIPELINE_IMAGE`) and `--skip-deploy` (run the recorded revision as-is); a release id that does not match the recorded revision's image fails closed.
- Client `env.sh` files must set `PIPELINE_IMAGE` as an overridable default, `export PIPELINE_IMAGE="${PIPELINE_IMAGE:-<repository@sha256>}"`, so the wrapper scripts can pass a freshly built digest through to `deploy-client.sh`.
- When taskdef wiring and container behavior change together, build a fresh release, then render/register/activate before smoke testing.
- For manual smoke runs, use a one-off `aws ecs run-task` against the recorded runtime revision ARN and tail `/ecs/mssp-pipeline` logs.

## Testing

```bash
uv run pytest
uv run pytest tests/integration/ -v
uv run pytest tests/processing/ -v
```

- **Mock `acoms-cli`** subprocess calls — never call the real binary in tests.
- **Use PARQUET/DUCKDB output** for local development — avoid cloud backends.

## Binary

| Platform | Path |
|---|---|
| macOS | `bin/acoms-cli` |
| Linux | `bin/acoms-cli-linux` (copied to `/app/bin/acoms-cli` in Docker) |

Requires `config.txt` in working directory. Generate with `mssp-download --configure`. **Do not commit.**

## Architecture

```
mssp_pipeline/
├── config.py           # loads .env, provides typed RuntimeConfig
├── pipeline.py         # orchestrator (download → process)
├── __main__.py         # CLI entry points
├── integration/        # download: cli, downloader, parser, state, s3_uploader
└── processing/        # ETL: session, defs/, processors/, exporters/
```

DuckDB reads files directly (zipfs, webbed, rusty_sheet, excel extensions) — no temp extraction.

## Key Conventions

- **Incremental by default**: tracks `FILE_PATH` per table, only appends new rows.
- **Lazy cloud imports**: heavy SDKs loaded only when matching `OUTPUT_TYPE` is active.
- **DuckDB temp spill**: ECS runtime uses local scratch (`MSSP_TEMP_LOCATION`, usually `/tmp/mssp-staging`) and DuckDB `temp_directory` points there for spill-to-disk.
- **Snowflake ECS secret handling**: runtime accepts `SNOWFLAKE_RSA_KEY` as raw PEM or base64-encoded PEM; entrypoint materializes `/tmp/snowflake_rsa_key.p8` and exports `SNOWFLAKE_RSA_KEY_PATH`.
- **Foundation gate parameters**: Terraform creates `/mssp/bootstrap_complete` and `/mssp/whitelist_confirmed` with initial `false`, but now ignores later value changes so bootstrap/ops can flip them to `true` without later foundation applies resetting them.
- **Readiness gates are checked, not asserted**: the `readiness-gates` container gets `MSSP_READINESS_BOOTSTRAP` / `MSSP_READINESS_WHITELIST` as ECS `secrets` (`valueFrom` = the SSM parameter ARN, rendered from `<READINESS_<GATE>_PARAM_ARN>`), never as plain `environment` values. The task execution role needs `ssm:GetParameters` on exactly those two parameters (`stage_iam.tf`).
- **uv.lock is committed on purpose** — the image builds `uv sync --frozen` from it, so a clean checkout reproduces the same dependency set. When dependencies change, regenerate it (`uv lock`) and commit the updated lockfile alongside `pyproject.toml`; never revert or drop it.
- Terraform `.terraform/`, `terraform.tfstate*`, and crash logs should stay untracked.
- `config.txt`, `state.json`, `downloads/`, `STAGED/`, `.runs/` gitignored.
