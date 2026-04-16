#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/build-and-push-image.sh <client> [tag]

Examples:
  scripts/build-and-push-image.sh vbca
  scripts/build-and-push-image.sh vbca 2026-04-14

Environment overrides:
  AWS_REGION        AWS region (falls back to client env.sh or aws config)
  AWS_PROFILE       AWS profile (optional)
  MSSP_ECR_REPO     ECR repository name (default: mssp-pipeline)
EOF
}

CLIENT="${1:-}"
TAG="${2:-${IMAGE_TAG:-latest}}"
if [[ -z "$CLIENT" ]]; then
  usage
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT_DIR="$ROOT_DIR/infra/clients/$CLIENT"
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
IMAGE_URI="$REGISTRY/$REPO:$TAG"

echo "[info] region=$REGION account_id=$ACCOUNT_ID repo=$REPO tag=$TAG"

aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "$REPO" --region "$REGION" >/dev/null

echo "[info] Logging in to ECR..."
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null

echo "[info] Building and pushing linux/amd64 image: $IMAGE_URI"
docker buildx build \
  --platform linux/amd64 \
  --tag "$IMAGE_URI" \
  --push \
  "$ROOT_DIR"

echo "[ok] Pushed image: $IMAGE_URI"
