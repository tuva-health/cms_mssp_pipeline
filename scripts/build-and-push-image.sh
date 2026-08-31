#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/build-and-push-image.sh <client> <release-id> [metadata-file]

Builds the pipeline runtime image from a CLEAN checkout, pushes it under an
immutable release tag, resolves the pushed repository@sha256 digest, and writes
release-provenance metadata (image digest + full source commit + dependency
checksum).

The build is deterministic: the base image is digest-pinned, dependencies are
installed frozen from uv.lock, and the bundled CMS CLI is checksum-verified.

Environment overrides:
  AWS_REGION       AWS region (falls back to client env.sh or aws config)
  AWS_PROFILE      AWS profile (optional)
  MSSP_ECR_REPO    ECR repository name (default: mssp-pipeline)
  PIP_EXTRAS       Python extras to bake in (auto-derived from MSSP_OUTPUT_TYPE)
EOF
}

CLIENT="${1:-}"
RELEASE_ID="${2:-}"
if [[ -z "$CLIENT" || -z "$RELEASE_ID" ]]; then
  usage
  exit 1
fi
if [[ ! "$RELEASE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "[error] Invalid release id: $RELEASE_ID" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT_DIR="$ROOT_DIR/infra/clients/$CLIENT"
METADATA_FILE="${3:-$ROOT_DIR/release-metadata/$RELEASE_ID.json}"
[[ -d "$CLIENT_DIR" ]] || { echo "[error] Client overlay not found: $CLIENT_DIR" >&2; exit 1; }

ENV_FILE="$CLIENT_DIR/env.sh"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_FILE"
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "[error] Required command not found: $1" >&2; exit 1; }
}

require_cmd aws
require_cmd docker
require_cmd git
require_cmd python3
require_cmd shasum

# Release builds must reproduce from source alone: refuse a dirty checkout.
if ! git -C "$ROOT_DIR" diff --quiet || ! git -C "$ROOT_DIR" diff --cached --quiet || \
  [[ -n "$(git -C "$ROOT_DIR" ls-files --others --exclude-standard)" ]]; then
  echo "[error] Release builds require a clean checkout." >&2
  exit 1
fi

# Verify the bundled CMS binaries before they are baked into the image.
(
  cd "$ROOT_DIR"
  shasum -a 256 -c release/cms-binaries.sha256
)

extras_for_output_type() {
  local output_type
  output_type="$(echo "${1:-PARQUET}" | tr '[:lower:]' '[:upper:]')"
  case "$output_type" in
    SNOWFLAKE)   echo "processing,snowflake" ;;
    DATABRICKS)  echo "processing,databricks" ;;
    BIGQUERY)    echo "processing,bigquery" ;;
    REDSHIFT)    echo "processing,redshift" ;;
    FABRIC)      echo "processing,fabric" ;;
    PARQUET|DUCKDB|MOTHERDUCK) echo "processing" ;;
    *)           echo "processing" ;;
  esac
}

SOURCE_COMMIT="$(git -C "$ROOT_DIR" rev-parse HEAD)"
DEPENDENCY_CHECKSUM="$(shasum -a 256 "$ROOT_DIR/uv.lock" | cut -d ' ' -f 1)"
PIP_EXTRAS_VALUE="${PIP_EXTRAS:-$(extras_for_output_type "${MSSP_OUTPUT_TYPE:-PARQUET}")}"

REGION="${AWS_REGION:-${REGION:-}}"
if [[ -z "$REGION" ]]; then
  REGION="$(aws configure get region 2>/dev/null || true)"
fi
if [[ -z "$REGION" ]]; then
  echo "[error] AWS region is not set. Set AWS_REGION in env or $ENV_FILE." >&2
  exit 1
fi

ACCOUNT_ID="${ACCOUNT_ID:-}"
if [[ -z "$ACCOUNT_ID" ]]; then
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
fi

REPO="${MSSP_ECR_REPO:-mssp-pipeline}"
REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
REPOSITORY="$REGISTRY/$REPO"
TAGGED_IMAGE="$REPOSITORY:$RELEASE_ID"

# The repository must reject mutable tags so a release id resolves to one digest.
MUTABILITY="$(aws ecr describe-repositories \
  --repository-names "$REPO" \
  --region "$REGION" \
  --query 'repositories[0].imageTagMutability' \
  --output text)"
if [[ "$MUTABILITY" != "IMMUTABLE" ]]; then
  echo "[error] ECR repository $REPO must enforce immutable tags." >&2
  exit 1
fi

echo "[info] release=$RELEASE_ID source=$SOURCE_COMMIT deps=$DEPENDENCY_CHECKSUM extras=$PIP_EXTRAS_VALUE"
aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "$REGISTRY" >/dev/null

docker buildx build \
  --platform linux/amd64 \
  --build-arg "SOURCE_COMMIT=$SOURCE_COMMIT" \
  --build-arg "RELEASE_ID=$RELEASE_ID" \
  --build-arg "DEPENDENCY_CHECKSUM=$DEPENDENCY_CHECKSUM" \
  --build-arg "PIP_EXTRAS=$PIP_EXTRAS_VALUE" \
  --tag "$TAGGED_IMAGE" \
  --push \
  "$ROOT_DIR"

DIGEST="$(aws ecr describe-images \
  --repository-name "$REPO" \
  --region "$REGION" \
  --image-ids "imageTag=$RELEASE_ID" \
  --query 'imageDetails[0].imageDigest' \
  --output text)"
if [[ ! "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "[error] ECR returned an invalid image digest: $DIGEST" >&2
  exit 1
fi

mkdir -p "$(dirname "$METADATA_FILE")"
python3 - \
  "$METADATA_FILE" \
  "$REPOSITORY" \
  "$DIGEST" \
  "$SOURCE_COMMIT" \
  "$RELEASE_ID" \
  "$DEPENDENCY_CHECKSUM" <<'PY'
import json
from pathlib import Path
import sys

metadata_path, repository, digest, source_commit, release_id, dependency_checksum = sys.argv[1:]
image_uri = f"{repository}@{digest}"
if "@sha256:" not in image_uri:
    raise SystemExit(f"Refusing mutable image reference: {image_uri}")
metadata = {
    "image": image_uri,
    "source_commit": source_commit,
    "release_id": release_id,
    "dependency_checksum": dependency_checksum,
}
Path(metadata_path).write_text(json.dumps(metadata, indent=2) + "\n")
PY

echo "[ok] Released immutable image: $REPOSITORY@$DIGEST"
echo "[ok] Wrote release metadata: $METADATA_FILE"
