# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

Combined MSSP ACO pipeline — two subsystems in one repo:

1. **Integration** (`mssp_pipeline/integration/`): Downloads MSSP ACO files from the CMS ACO Datahub by wrapping the `acoms-cli` binary. Extracts zip archives. Optionally uploads to S3.

2. **Processing** (`mssp_pipeline/processing/`): ETL pipeline. Reads downloaded files via DuckDB (zipfs, webbed, rusty_sheet extensions) and exports structured tables to one of 8 output backends.

## Quick Start

```bash
uv sync --group dev

# First-time credential setup
uv run mssp-download --configure

# Download only
uv run mssp-download --aco=C1234 --start-year=2020 --mode=incremental

# Process only (edit mssp_pipeline/config.py first)
uv run mssp-process

# Full pipeline: download then process
uv run mssp-pipeline --aco=C1234 --start-year=2020 --mode=incremental
```

## Configuration

All settings live in `mssp_pipeline/config.py`. Edit it before running:

| Variable | Purpose |
|---|---|
| `ACO_ID` | ACO identifier (e.g. `C1234`) — shared by both subsystems |
| `FILE_STORE` | Source/output directory — shared by both subsystems |
| `START_YEAR` | First performance year for downloads |
| `OUTPUT_TYPE` | Processing output: `PARQUET`, `DUCKDB`, `MOTHERDUCK`, `SNOWFLAKE`, `DATABRICKS`, `BIGQUERY`, `REDSHIFT`, or `FABRIC` |
| `FULL_REFRESH` | `False` (incremental, default) or `True` (drop and recreate all tables) |

Override any setting with environment variables: `MSSP_ACO_ID`, `MSSP_FILE_STORE`, `MSSP_OUTPUT_TYPE`, `MSSP_OUTPUT_LOCATION`, `MSSP_FULL_REFRESH`.

## Development

```bash
uv sync --group dev

# Run all tests
uv run pytest

# Run integration subsystem tests only
uv run pytest tests/integration/ -v

# Run processing subsystem tests only
uv run pytest tests/processing/ -v
```

**Do NOT call the actual `acoms-cli` binary during testing.** Mock all subprocess calls.

**Do NOT test with cloud output backends** (`SNOWFLAKE`, `DATABRICKS`, etc.) in development.

## Architecture

```
mssp_pipeline/
├── config.py           ← edit this: ACO_ID, FILE_STORE, OUTPUT_TYPE, etc.
├── pipeline.py         ← end-to-end orchestrator (download → process)
├── __main__.py         ← CLI: mssp-download, mssp-process, mssp-pipeline
│
├── integration/        ← download subsystem
│   ├── cli.py          ← subprocess wrapper for acoms-cli (with retry)
│   ├── config.py       ← IntegrationConfig dataclass
│   ├── downloader.py   ← orchestrates list → view → download → extract
│   ├── parser.py       ← regex parsing of acoms-cli output
│   ├── s3_uploader.py  ← upload extracted files to S3
│   └── state.py        ← JSON state tracking (local or S3)
│
└── processing/         ← ETL subsystem
    ├── __init__.py     ← run(config=None) entry point
    ├── config.py       ← processing-specific settings (read by run())
    ├── config_defs.py  ← frozen dataclasses for cloud backend configs
    ├── session.py      ← DuckDB connection + extension loading
    ├── defs/           ← file definition dataclasses (one per source type)
    ├── processors/     ← FileProcessor subclasses (Template Method pattern)
    └── exporters/      ← Exporter implementations (Strategy pattern)
```

## Binary

`bin/acoms-cli` is the CMS-provided CLI binary (~68 MB). It is tracked in the repo.
If using Git LFS, run `git lfs track "bin/acoms-cli"` before committing.

The binary requires `config.txt` (API credentials) in the current working directory.
Run `mssp-download --configure` once to create it. Do not commit `config.txt`.
