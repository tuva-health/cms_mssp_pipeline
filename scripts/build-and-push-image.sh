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

The build itself is scripts/build-image.sh (shared with the tag-triggered
release workflow): the base image is digest-pinned, dependencies are installed
frozen from uv.lock, the bundled CMS CLI is checksum-verified, and timestamps
are pinned to the commit time. The digest buildx pushed is cross-checked
against ECR before the metadata is kept.

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

# Refuse a dirty checkout or a bad CMS binary before any registry call.
"$ROOT_DIR/scripts/build-image.sh" "${MSSP_ECR_REPO:-mssp-pipeline}" "$RELEASE_ID" --check-only

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

aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "$REGISTRY" >/dev/null

# One build recipe for every release path: clean-checkout guard, CMS binary
# verification, extras, provenance build args and reproducibility settings all
# live in scripts/build-image.sh. It also writes the release metadata, naming
# the digest buildx pushed.
BUILD_RECORD="$(mktemp)"
trap 'rm -f "$BUILD_RECORD"' EXIT
# env.sh may set MSSP_OUTPUT_TYPE / PIP_EXTRAS without exporting them.
MSSP_OUTPUT_TYPE="${MSSP_OUTPUT_TYPE:-}" PIP_EXTRAS="${PIP_EXTRAS:-}" \
  "$ROOT_DIR/scripts/build-image.sh" "$REPOSITORY" "$RELEASE_ID" \
  --push \
  --metadata "$METADATA_FILE" \
  --record "$BUILD_RECORD"
BUILT_DIGEST="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["manifest_digest"])' "$BUILD_RECORD")"

# Cross-check against the registry: the metadata must name exactly what ECR holds.
DIGEST="$(aws ecr describe-images \
  --repository-name "$REPO" \
  --region "$REGION" \
  --image-ids "imageTag=$RELEASE_ID" \
  --query 'imageDetails[0].imageDigest' \
  --output text)"
if [[ ! "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  rm -f "$METADATA_FILE"
  echo "[error] ECR returned an invalid image digest: $DIGEST" >&2
  exit 1
fi
if [[ "$DIGEST" != "$BUILT_DIGEST" ]]; then
  rm -f "$METADATA_FILE"
  echo "[error] ECR digest $DIGEST does not match the built digest $BUILT_DIGEST" >&2
  exit 1
fi

echo "[ok] Released immutable image: $REPOSITORY@$DIGEST"
echo "[ok] Wrote release metadata: $METADATA_FILE"
