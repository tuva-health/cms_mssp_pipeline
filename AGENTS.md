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
- **uv.lock gitignored** — do not commit lockfile.
- `config.txt`, `state.json`, `downloads/`, `STAGED/`, `.runs/` gitignored.
