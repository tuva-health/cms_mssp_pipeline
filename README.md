# mssp_pipeline

Combined MSSP ACO data pipeline. Downloads CMS ACO Datahub files and processes them into structured tables, with runtime configuration loaded from a project `.env` file.

## Overview

Two subsystems, one package:

1. **Integration** — wraps the `acoms-cli` binary to list, download, and extract MSSP ACO zip archives from the CMS Datahub. Supports incremental runs (only fetches new files) and optional S3 upload.

2. **Processing** — reads the downloaded files with DuckDB and exports structured tables to one of 8 output backends. Supports 8 source file types. Incremental by default: only rows from new source files are appended.

Both subsystems share `ACO_ID`, `FILE_STORE`, and cloud credentials from the same `.env` file.

---

## Quick Start

### Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) for dependency management
- `config.txt` in the project root (CMS Datahub API credentials — see [First-Time Setup](#first-time-setup))

### Install

```bash
git clone <repo-url>
cd mssp_pipeline
uv sync --group dev
```

Create a local environment file from the example template:

```bash
cp .env.example .env
```

To include a cloud output backend, add its extra:

```bash
uv sync --group dev --extra processing --extra snowflake
uv sync --group dev --extra processing --extra bigquery
uv sync --group dev --extra processing --extra databricks
# etc.
```

### First-Time Setup

Run the interactive credential setup for the CMS Datahub CLI:

```bash
uv run mssp-download --configure
```

This creates `config.txt` in the project root. Do not commit this file.

### Configure `.env`

Set at minimum:

```dotenv
MSSP_ACO_ID=A1234
MSSP_FILE_STORE=/path/to/data
MSSP_OUTPUT_TYPE=PARQUET
MSSP_OUTPUT_LOCATION=/path/to/output/parquet
```

Use `.env` for environment-specific settings such as ACO IDs, file stores, credentials, and backend destinations. `mssp_pipeline/config.py` loads `.env` automatically, so you should not need to edit Python config for normal setup.

### Run

```bash
# Download only
uv run mssp-download

# Process only (files must already exist at FILE_STORE)
uv run mssp-process

# Full pipeline: download then process
uv run mssp-pipeline

# Validate configuration and local prerequisites
uv run mssp-validate --target pipeline
```

### AWS deployment guides

- `docs/aws-staged-deployment-plan.md` — Foundation → Bootstrap → Activate rollout
- `docs/aws-bootstrap-runbook.md` — operator checklist for whitelist + `config.txt` bootstrap
- `docs/aws-ecs-container-contract.md` — ECS env/secret contract and container behavior
- `docs/aws-iam-minimum-policies.md` — least-privilege baseline IAM templates
- `infra/aws/ecs/taskdef-runtime.json` and `infra/aws/ecs/taskdef-bootstrap.json` — ECS task definition templates (x86_64)
- `infra/terraform/aws/README.md` — Terraform skeleton usage for Foundation + Activate
- `infra/clients/client.example/` — per-client overlay examples (`env.sh`, `*.tfvars`)
- `scripts/check-client-config.sh` — validates AWS auth, required vars, and per-client tfvars before deploy
- `scripts/build-and-push-image.sh` — builds and pushes linux/amd64 image to ECR for a client context; automatically installs backend extras based on `MSSP_OUTPUT_TYPE` (override with `PIP_EXTRAS`)
- `scripts/deploy-client.sh` — wrapper to validate + apply foundation/activate and render/register ECS taskdefs; `activate` automatically resolves the latest active `mssp-pipeline-runtime` revision

Recommended ECS rollout:

```bash
export IMAGE_TAG=2026-04-17-my-change
scripts/build-and-push-image.sh <client> "$IMAGE_TAG"
scripts/deploy-client.sh <client> render-taskdefs
scripts/deploy-client.sh <client> register-taskdefs
scripts/deploy-client.sh <client> activate
```

Use a fresh image tag when task definition wiring and container behavior change together (for example entrypoint/env/secret handling changes), then run a one-off smoke task before relying on the schedule.

---

## CLI Reference

All three commands accept overrides for the most common `.env` values:

### `mssp-download`

```
--aco ACO_ID           ACO identifier (default: MSSP_ACO_ID)
--start-year YEAR      First performance year to download (default: MSSP_START_YEAR or config default)
--mode incremental|full
                       Incremental skips already-downloaded files (default: incremental)
--output-dir DIR       Local directory for downloaded files (default: downloads/)
--state-file FILE      Path to download state JSON (default: state.json)
--cli-path PATH        Path to acoms-cli binary (default: bin/acoms-cli)
--s3-bucket BUCKET     Upload extracted files to this S3 bucket and delete local copies
--configure            Run interactive acoms-cli credential setup and exit
--reset-state          Clear download state and re-download everything
```

### `mssp-process`

```
--aco ACO_ID           Override MSSP_ACO_ID
--file-store DIR       Override MSSP_FILE_STORE (source directory override)
--output-type TYPE     Override MSSP_OUTPUT_TYPE
--full-refresh         Drop and recreate all output tables (default: incremental)
```

### `mssp-pipeline`

Accepts all arguments from both commands above, plus:

```
--download-dir DIR     Local intermediate directory for downloaded files before processing
--skip-download        Skip the download phase (process only)
--skip-process         Skip the processing phase (download only)
--cleanup-download-dir Delete local download-dir after run (opt-in)
--run-id ID            Optional run id for manifest file naming
--resume-run-id ID     Resume from prior run (skip phases marked completed)
--resume-latest        Resume from the newest manifest in --manifest-dir
--manifest-dir DIR     Directory for run manifests (default: .runs)
```

### `mssp-validate`

```
--target download|process|pipeline
                       Validate only one phase (default: pipeline)
--cli-path PATH        Optional acoms-cli path override for download checks
--format text|json     Human-readable or machine-readable output (default: text)
--strict               Treat warnings as failures (non-zero exit)
--live                 Perform live filesystem/credential checks when possible
```

`mssp-validate` warnings (non-fatal by default, fatal with `--strict`):

- `MSSP_START_YEAR` is in the future (likely returns no files)
- `MSSP_FILE_STORE` is a relative local path (works locally, less reliable for scheduled/CI runs)
- Cloud `MSSP_OUTPUT_TYPE` with default `MSSP_TEMP_LOCATION=./STAGED` (recommend explicit staging path)
- `MSSP_S3_BUCKET` and `MSSP_FILE_STORE` appear to point to different stores (legacy override may be confusing)

Each pipeline run writes a manifest at `.runs/<run-id>.json` with phase status,
errors, and event timestamps for auditability and resume support. At run end,
the CLI also prints a one-line summary with run id, elapsed time, phase status,
and event count.

JSON output example (useful for scripts/automation):

```bash
uv run mssp-validate --target pipeline --format json --strict
```

```json
{
  "target": "pipeline",
  "strict": true,
  "live": false,
  "ok": false,
  "info": ["acoms-cli found: /path/to/bin/acoms-cli"],
  "warnings": ["MSSP_FILE_STORE='downloads' is a relative path; use an absolute path for scheduled/CI runs"],
  "errors": []
}
```

### `mssp-runs`

Inspect run manifests written by `mssp-pipeline`.

```
--manifest-dir DIR     Directory containing run manifests (default: .runs)
--limit N              Max runs to list (newest first, default: 20)
--run-id ID            Show details for a single run id
--format text|json     Human-readable or machine-readable output (default: text)
```

Examples:

```bash
# List latest runs
uv run mssp-runs

# Inspect a specific run
uv run mssp-runs --run-id 20260414T153000Z
```

### Environment variable overrides

`.env` values are loaded first, then any process environment variables override them at runtime:

| Variable | Purpose |
|---|---|
| `MSSP_ACO_ID` | Shared ACO identifier |
| `MSSP_FILE_STORE` | Shared source/download store |
| `MSSP_START_YEAR` | First performance year to download |
| `MSSP_DOWNLOAD_MODE` | `incremental` or `full` download mode |
| `MSSP_OUTPUT_TYPE` | Export backend |
| `MSSP_OUTPUT_LOCATION` | Output path for `PARQUET` or `DUCKDB` |
| `MSSP_TEMP_LOCATION` | Local staging directory for cloud exporters |
| `MSSP_FULL_REFRESH` | Full rebuild when set to `1`, `true`, or `yes` |
| `MSSP_S3_BUCKET` | Optional alternate S3 bucket for downloads/state |
| `SNOWFLAKE_*`, `DATABRICKS_*`, `BIGQUERY_*`, `REDSHIFT_*`, `FABRIC_*`, `MOTHERDUCK_*` | Exporter-specific settings |
| `AWS_*`, `AZURE_*`, `GCS_*` | Cloud file store settings |

---

## Configuration

Most day-to-day settings should live in `.env`. `mssp_pipeline/config.py` remains the defaults/loader layer, but normal project setup should happen through environment variables.

### Shared

```dotenv
MSSP_ACO_ID=A1234
MSSP_FILE_STORE=/path/to/downloads
```

| Variable | Description |
|---|---|
| `MSSP_ACO_ID` | Your ACO identifier (e.g. `A1234`) — shared by both subsystems |
| `MSSP_FILE_STORE` | Where organised ACO files live — local path, `s3://bucket/prefix`, `az://container/prefix`, `abfss://...`, or `gs://bucket/prefix` |

### Processing output

```dotenv
MSSP_OUTPUT_TYPE=PARQUET
MSSP_FULL_REFRESH=false
MSSP_OUTPUT_LOCATION=~/.data/output
MSSP_TEMP_LOCATION=./STAGED
```

### AWS (when FILE_STORE is s3://)

```dotenv
AWS_REGION=us-east-1
AWS_PROFILE=my-profile
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
```

### Azure (when FILE_STORE is az:// or abfss://)

```dotenv
# Option A - connection string
AZURE_STORAGE_CONNECTION_STRING='DefaultEndpointsProtocol=https;...'

# Option B - credential chain (managed identity, Azure CLI, env vars)
AZURE_STORAGE_ACCOUNT=mystorageaccount
```

### Download

```dotenv
MSSP_START_YEAR=2025
MSSP_DOWNLOAD_MODE=incremental
MSSP_S3_BUCKET=
```

---

## Output Backends

Select with `MSSP_OUTPUT_TYPE` in `.env`. Each backend requires its own variables.

| `OUTPUT_TYPE` | Destination | Extra to install | Auth |
|---|---|---|---|
| `PARQUET` | Local or cloud filesystem | `--extra processing` | — |
| `DUCKDB` | Local DuckDB file | `--extra processing` | — |
| `MOTHERDUCK` | MotherDuck (cloud DuckDB) | `--extra processing` | MotherDuck token |
| `SNOWFLAKE` | Snowflake table | `--extra snowflake` | RSA key pair |
| `DATABRICKS` | Unity Catalog / Hive table | `--extra databricks` | Personal access token |
| `BIGQUERY` | BigQuery dataset | `--extra bigquery` | Service account JSON |
| `REDSHIFT` | Redshift table | `--extra redshift` | IAM role + S3 staging |
| `FABRIC` | Power BI Fabric lakehouse | `--extra fabric` | Service principal or managed identity |

All backends support incremental mode. The pipeline tracks which source files have already been loaded (by `FILE_PATH`) and only appends rows from new files.

### Example `.env` blocks for every exporter

#### `PARQUET`

```dotenv
MSSP_OUTPUT_TYPE=PARQUET
MSSP_OUTPUT_LOCATION=/path/to/output/parquet
```

#### `DUCKDB`

```dotenv
MSSP_OUTPUT_TYPE=DUCKDB
MSSP_OUTPUT_LOCATION=~/.data/mssp.duckdb
```

#### `MOTHERDUCK`

```dotenv
MSSP_OUTPUT_TYPE=MOTHERDUCK
MOTHERDUCK_DATABASE=mssp_raw
MOTHERDUCK_TOKEN=md_token_here
```

#### `SNOWFLAKE`

```dotenv
MSSP_OUTPUT_TYPE=SNOWFLAKE
MSSP_TEMP_LOCATION=./STAGED
SNOWFLAKE_USERNAME=svc_mssp
SNOWFLAKE_ACCOUNT=acme-org.us-east-1
SNOWFLAKE_DATABASE=MSSP
SNOWFLAKE_SCHEMA=RAW_DATA
SNOWFLAKE_COMPUTE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_ACCOUNT_ROLE=ACCOUNTADMIN
SNOWFLAKE_RSA_KEY_PATH=~/.ssh/snowflake_rsa_key.p8
SNOWFLAKE_RSA_KEY_PASSPHRASE=
```

For ECS/ECR deployments, inject the private key via Secrets Manager/ECS Secrets and let `mssp-entrypoint` materialize `/tmp/snowflake_rsa_key.p8` at runtime instead of baking a key into the image. The secret may contain raw PEM text or base64-encoded PEM. `scripts/deploy-client.sh` renders Snowflake runtime tasks with `SNOWFLAKE_RSA_KEY` plus optional `SNOWFLAKE_RSA_KEY_PASSPHRASE`.

#### `DATABRICKS`

```dotenv
MSSP_OUTPUT_TYPE=DATABRICKS
MSSP_TEMP_LOCATION=./STAGED
DATABRICKS_SERVER_HOSTNAME=adb-1234567890123456.7.azuredatabricks.net
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/abc123def456
DATABRICKS_ACCESS_TOKEN=dapiXXXXXXXXXXXXXXXX
DATABRICKS_CATALOG=main
DATABRICKS_SCHEMA=raw_data
DATABRICKS_STAGING_PATH=dbfs:/tmp/mssp-staging
```

#### `BIGQUERY`

```dotenv
MSSP_OUTPUT_TYPE=BIGQUERY
MSSP_TEMP_LOCATION=./STAGED
BIGQUERY_PROJECT_ID=my-gcp-project
BIGQUERY_DATASET_ID=raw_data
BIGQUERY_STAGING_BUCKET=gs://my-mssp-staging
BIGQUERY_CREDENTIALS_PATH=/path/to/service-account.json
BIGQUERY_LOCATION=US
```

#### `REDSHIFT`

```dotenv
MSSP_OUTPUT_TYPE=REDSHIFT
MSSP_TEMP_LOCATION=./STAGED
REDSHIFT_HOST=example-cluster.abc123.us-east-1.redshift.amazonaws.com
REDSHIFT_PORT=5439
REDSHIFT_DATABASE=dev
REDSHIFT_SCHEMA=raw_data
REDSHIFT_USER=etl_user
REDSHIFT_PASSWORD=super-secret-password
REDSHIFT_IAM_ROLE=arn:aws:iam::123456789012:role/RedshiftCopyRole
REDSHIFT_STAGING_BUCKET=s3://my-mssp-staging
```

#### `FABRIC`

```dotenv
MSSP_OUTPUT_TYPE=FABRIC
MSSP_TEMP_LOCATION=./STAGED
FABRIC_ONELAKE_PATH=abfss://MyWorkspace@onelake.dfs.fabric.microsoft.com/MyLakehouse.Lakehouse/Tables
FABRIC_TENANT_ID=00000000-0000-0000-0000-000000000000
FABRIC_CLIENT_ID=11111111-1111-1111-1111-111111111111
FABRIC_CLIENT_SECRET=client-secret
```

---

## Source File Types

The processing subsystem handles 8 MSSP ACO file types automatically. Files are discovered by pattern under `FILE_STORE/ACO_ID/YEAR/`.

| File type | Description | Format | Output tables |
|---|---|---|---|
| CCLF | Comprehensive Claim & Line Feed | Fixed-width text | 10+ `parta_*` / `partb_*` tables |
| MSSP (ALR / BEUR / BAIP / NCBP) | Assignment and financial reports | CSV in zip | `AALR1_ASSIGNED_BENEFICIARIES`, `BEUR_*`, and others |
| MCQM | Medicare Clinical Quality Measures | XLSX in zip through PY2025; CSV files in nested MCQM zip starting PY2026 | `MCQM_BENEFICIARIES`, `MCQM_DM_001SSP`, and others |
| EXPU | Quarterly Expenditure & Utilization | XLSX | `EXPU_TABLE_1`, `EXPU_TABLE_2`, `EXPU_TABLE_3` |
| BNEX | Beneficiary Nested Expenditure | CSV in zip | `BNEX_BENEFICIARY_NESTED_EXPENDITURE` |
| BNEX MBI Xref | MBI cross-reference | CSV in zip | `BNEX_MBI_XREF` |
| Shadow Bundles | Episode-level bundled payment data | CSV in zip | `SHADOW_BUNDLES` |
| Participant List | ACO participant physician roster | XLSX | `PARTICIPANT_ROSTER` |

Every output table includes `FILE_PATH`, `FILE_NAME`, `DIRECTORY_NAME`, and `FILE_DATE` metadata columns for lineage tracking and incremental deduplication.

---

## Architecture

```
mssp_pipeline/
├── config.py           ← loads .env and provides shared defaults
├── pipeline.py         ← end-to-end orchestrator (download → process)
├── __main__.py         ← CLI: mssp-download, mssp-process, mssp-pipeline
│
├── integration/        ← download subsystem
│   ├── cli.py          ← subprocess wrapper for acoms-cli (with retry)
│   ├── downloader.py   ← orchestrates list → view → download → extract
│   ├── parser.py       ← regex parsing of acoms-cli output
│   ├── s3_uploader.py  ← optional S3 upload and local cleanup
│   └── state.py        ← JSON state tracking (local file or S3 object)
│
└── processing/         ← ETL subsystem
    ├── __init__.py     ← run(config) entry point
    ├── session.py      ← DuckDB connection and extension loader
    ├── defs/           ← file definition dataclasses (one per source type)
    ├── processors/     ← FileProcessor subclasses (Template Method pattern)
    └── exporters/      ← Exporter implementations (Strategy pattern)
```

### Key design decisions

**Shared environment config.** `.env` is loaded once and shared by both subsystems, so download and processing stay aligned on `MSSP_ACO_ID`, `MSSP_FILE_STORE`, and exporter credentials.

**DuckDB for all I/O.** Files are read directly by DuckDB using community extensions (`zipfs` for zip-embedded CSVs, `rusty_sheet` for S3-hosted xlsx, `excel` for local xlsx). No Python-side parsing or temp file extraction. In ECS/cloud-export runs, DuckDB temp spill is directed to local scratch via `MSSP_TEMP_LOCATION` (for example `/tmp/mssp-staging`).

**Incremental by default.** Every backend tracks loaded `FILE_PATH` values. Re-running the pipeline after new files arrive appends only the new rows.

**Lazy cloud imports.** Snowflake, Databricks, BigQuery, and other heavy SDKs are imported only when the matching `OUTPUT_TYPE` is active. A minimal install (download only, PARQUET output) requires no cloud SDKs.

---

## Development

```bash
uv sync --group dev

# Run all tests
uv run pytest

# Run subsystem tests independently
uv run pytest tests/integration/ -v
uv run pytest tests/processing/ -v
```

Tests use synthetic fixture data and mock all subprocess calls. Do not run tests against real cloud backends or the live CMS Datahub.

---

## Binary

`bin/acoms-cli` is the CMS-provided CLI binary used locally. For container/AWS deployments, place the Linux x86_64 binary at `bin/acoms-cli-linux` (the Docker build copies it to `/app/bin/acoms-cli`).

If you need to track binaries in git, use Git LFS:

```bash
git lfs track "bin/acoms-cli"
git lfs track "bin/acoms-cli-linux"
git add .gitattributes bin/acoms-cli bin/acoms-cli-linux
```

The CLI requires a `config.txt` file in the current working directory. Generate it once with `mssp-download --configure` (or use the AWS bootstrap flow that stores `config.txt` in Secrets Manager). Do not commit `config.txt`.

Terraform working directories and local state should also stay out of git. `.gitignore` excludes:
- `**/.terraform/`
- `*.tfstate`
- `*.tfstate.*`
- Terraform crash logs
