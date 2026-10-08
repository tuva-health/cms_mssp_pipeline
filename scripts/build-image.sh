#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/build-image.sh <repository> <release-id> [--push] [--metadata FILE] [--record FILE]

Builds the linux/amd64 pipeline runtime image from a CLEAN checkout as
<repository>:<release-id> and records what was built. This is the one build
recipe: scripts/build-and-push-image.sh calls it with --push for a client
registry, and .github/workflows/release.yml calls it without --push to prove a
tag builds and to record the digests a client build should reproduce.

  --push           Push <repository>:<release-id> (the caller logs in first).
                   Without it the image stays in the builder (docker-container
                   driver) or the local image store (docker driver).
  --metadata FILE  Write release-provenance metadata (the contract checked by
                   scripts/verify_release_metadata.py): image is
                   <repository>@<manifest digest>.
  --record FILE    Write the build record: build inputs (extras,
                   SOURCE_DATE_EPOCH) plus the manifest and config digests.

Reproducibility: SOURCE_DATE_EPOCH is the commit time of HEAD, layer file
timestamps are rewritten to it, and provenance/SBOM attestations are off, so
two builds of one commit with the same extras can produce the same digests.
See the README "Releases" section for what is and is not reproducible.

Environment:
  MSSP_OUTPUT_TYPE  Derives the Python extras to bake in (default PARQUET).
  PIP_EXTRAS        Overrides the derived extras.
EOF
}

REPOSITORY=""
RELEASE_ID=""
PUSH=false
METADATA_FILE=""
RECORD_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --push) PUSH=true; shift ;;
    --metadata) METADATA_FILE="${2:?--metadata needs a file}"; shift 2 ;;
    --record) RECORD_FILE="${2:?--record needs a file}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "[error] Unknown option: $1" >&2; usage >&2; exit 1 ;;
    *)
      if [[ -z "$REPOSITORY" ]]; then REPOSITORY="$1"
      elif [[ -z "$RELEASE_ID" ]]; then RELEASE_ID="$1"
      else echo "[error] Unexpected argument: $1" >&2; exit 1
      fi
      shift ;;
  esac
done
if [[ -z "$REPOSITORY" || -z "$RELEASE_ID" ]]; then
  usage >&2
  exit 1
fi
if [[ ! "$RELEASE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "[error] Invalid release id: $RELEASE_ID" >&2
  exit 1
fi
if [[ "$REPOSITORY" == *@* || "${REPOSITORY##*/}" == *:* ]]; then
  echo "[error] Repository must not carry a tag or digest; the release id is the tag: $REPOSITORY" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "[error] Required command not found: $1" >&2; exit 1; }
}

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
SOURCE_DATE_EPOCH="$(git -C "$ROOT_DIR" log -1 --format=%ct HEAD)"
DEPENDENCY_CHECKSUM="$(shasum -a 256 "$ROOT_DIR/uv.lock" | cut -d ' ' -f 1)"
PIP_EXTRAS_VALUE="${PIP_EXTRAS:-$(extras_for_output_type "${MSSP_OUTPUT_TYPE:-PARQUET}")}"
TAGGED_IMAGE="$REPOSITORY:$RELEASE_ID"

BUILD_METADATA="$(mktemp)"
trap 'rm -f "$BUILD_METADATA"' EXIT

echo "[info] release=$RELEASE_ID source=$SOURCE_COMMIT deps=$DEPENDENCY_CHECKSUM extras=$PIP_EXTRAS_VALUE epoch=$SOURCE_DATE_EPOCH push=$PUSH"

docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  --sbom=false \
  --build-arg "SOURCE_COMMIT=$SOURCE_COMMIT" \
  --build-arg "RELEASE_ID=$RELEASE_ID" \
  --build-arg "DEPENDENCY_CHECKSUM=$DEPENDENCY_CHECKSUM" \
  --build-arg "PIP_EXTRAS=$PIP_EXTRAS_VALUE" \
  --build-arg "SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH" \
  --output "type=image,name=$TAGGED_IMAGE,push=$PUSH,rewrite-timestamp=true" \
  --metadata-file "$BUILD_METADATA" \
  "$ROOT_DIR"

python3 - \
  "$BUILD_METADATA" \
  "$REPOSITORY" \
  "$SOURCE_COMMIT" \
  "$RELEASE_ID" \
  "$DEPENDENCY_CHECKSUM" \
  "$PIP_EXTRAS_VALUE" \
  "$SOURCE_DATE_EPOCH" \
  "$METADATA_FILE" \
  "$RECORD_FILE" <<'PY'
import json
import re
import sys
from pathlib import Path

(build_metadata, repository, source_commit, release_id, dependency_checksum,
 pip_extras, source_date_epoch, metadata_path, record_path) = sys.argv[1:]
built = json.loads(Path(build_metadata).read_text())
digest = built.get("containerimage.digest", "")
config_digest = built.get("containerimage.config.digest", "")
for label, value in (("manifest", digest), ("config", config_digest)):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise SystemExit(f"[error] buildx reported no {label} digest (got {value!r})")
image_uri = f"{repository}@{digest}"
if "@sha256:" not in image_uri:
    raise SystemExit(f"Refusing mutable image reference: {image_uri}")

if metadata_path:
    metadata = {
        "image": image_uri,
        "source_commit": source_commit,
        "release_id": release_id,
        "dependency_checksum": dependency_checksum,
    }
    Path(metadata_path).parent.mkdir(parents=True, exist_ok=True)
    Path(metadata_path).write_text(json.dumps(metadata, indent=2) + "\n")
if record_path:
    record = {
        "image": image_uri,
        "manifest_digest": digest,
        "config_digest": config_digest,
        "source_commit": source_commit,
        "release_id": release_id,
        "dependency_checksum": dependency_checksum,
        "pip_extras": pip_extras,
        "source_date_epoch": int(source_date_epoch),
        "platform": "linux/amd64",
    }
    Path(record_path).parent.mkdir(parents=True, exist_ok=True)
    Path(record_path).write_text(json.dumps(record, indent=2) + "\n")
print(f"[ok] Built {image_uri} (config {config_digest})")
PY
