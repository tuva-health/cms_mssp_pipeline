#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

load_env_file() {
  local env_file="$1"
  if [[ -f "${env_file}" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "${env_file}"
    set +a
  fi
}

load_env_file ".env"
load_env_file ".env.tier1-private"

: "${MSSP_ACO_ID:?Set MSSP_ACO_ID in .env.tier1-private}"
: "${MSSP_START_YEAR:?Set MSSP_START_YEAR in .env.tier1-private}"
: "${MSSP_FILE_STORE:?Set MSSP_FILE_STORE=s3://bucket/prefix in .env.tier1-private}"

if [[ "${MSSP_FILE_STORE}" != s3://* ]]; then
  echo "MSSP_FILE_STORE must be an s3:// URI for the Tier 1 private claims S3 run." >&2
  exit 1
fi

if [[ ! -f "config.txt" ]]; then
  echo "No config.txt found. Run: uv run mssp-download --configure" >&2
  exit 1
fi

uv run mssp-pipeline \
  --aco "${MSSP_ACO_ID}" \
  --start-year "${MSSP_START_YEAR}" \
  --mode "${MSSP_DOWNLOAD_MODE:-incremental}" \
  --file-store "${MSSP_FILE_STORE}" \
  --output-type "${MSSP_OUTPUT_TYPE:-PARQUET}" \
  "$@"
