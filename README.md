# mssp_pipeline

Combined MSSP ACO data pipeline. Downloads CMS ACO Datahub files and processes them into structured tables — in a single repo, with a single config file.

## Overview

Two subsystems, one package:

1. **Integration** — wraps the `acoms-cli` binary to list, download, and extract MSSP ACO zip archives from the CMS Datahub. Supports incremental runs (only fetches new files) and optional S3 upload.

2. **Processing** — reads the downloaded files with DuckDB and exports structured tables to one of 8 output backends. Supports 8 source file types. Incremental by default: only rows from new source files are appended.

Both subsystems share `ACO_ID`, `FILE_STORE`, and AWS credentials from a single `config.py`.

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

### Edit Configuration

Open `mssp_pipeline/config.py` and set at minimum:

```python
ACO_ID = 'A1234'              # Your ACO identifier
FILE_STORE = '/path/to/data'  # Local path, s3://bucket/prefix, or az://container/prefix
OUTPUT_TYPE = 'PARQUET'       # See Output Backends below
```

### Run

```bash
# Download only
uv run mssp-download

# Process only (files must already exist at FILE_STORE)
uv run mssp-process

# Full pipeline: download then process
uv run mssp-pipeline
```

---

## CLI Reference

All three commands accept overrides for the most common config values:

### `mssp-download`

```
--aco ACO_ID           ACO identifier (default: config.ACO_ID)
--start-year YEAR      First performance year to download (default: config.START_YEAR)
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
--aco ACO_ID           Override config.ACO_ID
--file-store DIR       Override config.FILE_STORE (source directory override)
--output-type TYPE     Override config.OUTPUT_TYPE
--full-refresh         Drop and recreate all output tables (default: incremental)
```

### `mssp-pipeline`

Accepts all arguments from both commands above, plus:

```
--download-dir DIR     Local intermediate directory for downloaded files before processing
--skip-download        Skip the download phase (process only)
--skip-process         Skip the processing phase (download only)
```

### Environment variable overrides

Any config value can be overridden at runtime without editing `config.py`:

| Variable | Config setting |
|---|---|
| `MSSP_ACO_ID` | `ACO_ID` |
| `MSSP_FILE_STORE` | `FILE_STORE` |
| `MSSP_OUTPUT_TYPE` | `OUTPUT_TYPE` |
| `MSSP_OUTPUT_LOCATION` | `OUTPUT_LOCATION` |
| `MSSP_FULL_REFRESH` | `FULL_REFRESH` (set to `1` or `true`) |
| `SNOWFLAKE_RSA_KEY_PASSPHRASE` | `RSA_KEY_PASSPHRASE` |

---

## Configuration

All settings live in `mssp_pipeline/config.py`. The most commonly edited fields:

### Shared

```python
ACO_ID = 'A1234'
FILE_STORE = '/path/to/downloads'  # local, s3://bucket/prefix, or az://container/prefix
```

| Variable | Description |
|---|---|
| `ACO_ID` | Your ACO identifier (e.g. `A1234`) — shared by both subsystems |
| `FILE_STORE` | Where organised ACO files live — local path, s3://bucket/prefix, or az://container/prefix |

### Processing output

```python
OUTPUT_TYPE = 'PARQUET'   # See Output Backends below
FULL_REFRESH = False      # True = drop and recreate all tables on every run
OUTPUT_LOCATION = '~/.data/output.duckdb'  # for PARQUET or DUCKDB outputs
TEMP_LOCATION = './STAGED'                 # for cloud outputs (staging parquet files)
```

### AWS (when FILE_STORE is s3://)

```python
AWS_REGION = 'us-east-1'
AWS_PROFILE = 'my-profile'   # Named profile from ~/.aws/credentials (recommended)
AWS_ACCESS_KEY_ID = ''        # Set only if not using a profile or IAM role
AWS_SECRET_ACCESS_KEY = ''
```

### Azure (when FILE_STORE is az:// or abfss://)

```python
# Option A — connection string
AZURE_STORAGE_CONNECTION_STRING = 'DefaultEndpointsProtocol=https;...'

# Option B — credential chain (managed identity, Azure CLI, env vars)
AZURE_STORAGE_ACCOUNT = 'mystorageaccount'
```

### Download

```python
START_YEAR = 2025
DOWNLOAD_MODE = 'incremental'   # or 'full'
S3_BUCKET = None                # Set to a bucket name to upload and delete local copies
```

---

## Output Backends

Select with `OUTPUT_TYPE` in `config.py`. Each backend requires its own config block — see the comments in `config.py` for all fields.

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

---

## Source File Types

The processing subsystem handles 8 MSSP ACO file types automatically. Files are discovered by pattern under `FILE_STORE/ACO_ID/YEAR/`.

| File type | Description | Format | Output tables |
|---|---|---|---|
| CCLF | Comprehensive Claim & Line Feed | Fixed-width text | 10+ `parta_*` / `partb_*` tables |
| MSSP (ALR / BEUR / BAIP / NCBP) | Assignment and financial reports | CSV in zip | `AALR1_ASSIGNED_BENEFICIARIES`, `BEUR_*`, and others |
| MCQM | Medicare Clinical Quality Measures | XLSX in zip | `MCQM_BENEFICIARIES`, `MCQM_DM_001SSP`, and others |
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
├── config.py           ← edit this: ACO_ID, FILE_STORE, OUTPUT_TYPE, credentials
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

**Single config file.** `ACO_ID` and `FILE_STORE` are defined once and shared by both subsystems — no drift between download destination and processing source.

**DuckDB for all I/O.** Files are read directly by DuckDB using community extensions (`zipfs` for zip-embedded CSVs, `rusty_sheet` for S3-hosted xlsx, `excel` for local xlsx). No Python-side parsing or temp file extraction.

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

`bin/acoms-cli` is the CMS-provided CLI binary (~68 MB, macOS arm64). It is not committed to git by default. If you need to track it, use Git LFS:

```bash
git lfs track "bin/acoms-cli"
git add .gitattributes bin/acoms-cli
```

The binary requires a `config.txt` file in the current working directory. Generate it once with `mssp-download --configure`. Do not commit `config.txt`.
