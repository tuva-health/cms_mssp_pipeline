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
scripts/build-and-push-image.sh <client> <tag>
scripts/deploy-client.sh <client> render-taskdefs
scripts/deploy-client.sh <client> register-taskdefs
scripts/deploy-client.sh <client> activate
```

One-command deploy + smoke test:

```bash
scripts/deploy-and-smoke-client.sh <client> <tag>
scripts/deploy-and-smoke-client.sh <client> <tag> -- mssp-validate --target process --strict
```

Conventions:
- `scripts/build-and-push-image.sh` derives Docker `PIP_EXTRAS` from `MSSP_OUTPUT_TYPE` (e.g. `processing,snowflake` for Snowflake).
- `scripts/deploy-client.sh activate` automatically resolves the latest active `mssp-pipeline-runtime` task definition ARN.
- `scripts/deploy-and-smoke-client.sh` wraps build/push + render/register/activate + one-off `aws ecs run-task` smoke execution against the latest runtime revision.
- Client `env.sh` files should use overridable defaults such as `export IMAGE_TAG="${IMAGE_TAG:-latest}"` so shell overrides work.
- When taskdef wiring and container behavior change together, use a fresh image tag, then render/register/activate before smoke testing.
- For manual smoke runs, use a one-off `aws ecs run-task` against the latest runtime revision and tail `/ecs/mssp-pipeline` logs.

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
- **uv.lock gitignored** — do not commit lockfile.
- Terraform `.terraform/`, `terraform.tfstate*`, and crash logs should stay untracked.
- `config.txt`, `state.json`, `downloads/`, `STAGED/`, `.runs/` gitignored.
