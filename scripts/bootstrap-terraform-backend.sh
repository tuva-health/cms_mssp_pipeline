#!/usr/bin/env bash
set -euo pipefail
#
# One-time remote-state backend bootstrap.
#
# Creates the dedicated, hardened Terraform state bucket (infra/terraform/aws/
# bootstrap) using a local state file, then migrates that local state into the
# bucket it just created via S3 native locking. After this runs, all roots use
# the S3 backend.
#
# This is a generic engine: the AWS account, region, state bucket, deployer
# principals, and the approval phrase are supplied by the client overlay and the
# environment -- nothing here is client-specific.

usage() {
  cat <<'EOF'
Usage:
  scripts/bootstrap-terraform-backend.sh <client> [--plan]

Requires (from the client overlay infra/clients/<client>/):
  bootstrap.tfvars         aws_account_id, aws_region, state_bucket_name, deployer_principal_arns
  bootstrap.backend.hcl    bucket, key, region, encrypt=true, use_lockfile=true

Environment:
  MSSP_APPROVE_BACKEND_BOOTSTRAP   must equal the overlay's BACKEND_BOOTSTRAP_APPROVAL
                                   (exported by the overlay env.sh) to proceed
  AWS_PROFILE / AWS credentials    an identity allowed to create the bucket
EOF
}

CLIENT="${1:-}"
MODE="${2:-apply}"
if [[ -z "$CLIENT" ]]; then usage; exit 1; fi
if [[ "$MODE" != "apply" && "$MODE" != "--plan" ]]; then usage; exit 1; fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT_DIR="$ROOT_DIR/infra/clients/$CLIENT"
BOOTSTRAP_DIR="$ROOT_DIR/infra/terraform/aws/bootstrap"
[[ -d "$CLIENT_DIR" ]] || { echo "[error] Client overlay not found: $CLIENT_DIR" >&2; exit 1; }

TFVARS="$CLIENT_DIR/bootstrap.tfvars"
BACKEND_HCL="$CLIENT_DIR/bootstrap.backend.hcl"
for f in "$TFVARS" "$BACKEND_HCL"; do
  [[ -f "$f" ]] || { echo "[error] Missing overlay file: $f" >&2; exit 1; }
done

ENV_FILE="$CLIENT_DIR/env.sh"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_FILE"
fi

require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "[error] Required command not found: $1" >&2; exit 1; }; }
require_cmd terraform
require_cmd aws

# Approval guard. The expected phrase is a client-owned value provided by the
# overlay; this script only checks that the operator supplied the matching one.
EXPECTED_APPROVAL="${BACKEND_BOOTSTRAP_APPROVAL:-}"
if [[ "$MODE" == "apply" ]]; then
  if [[ -z "$EXPECTED_APPROVAL" ]]; then
    echo "[error] Overlay must export BACKEND_BOOTSTRAP_APPROVAL (env.sh) to authorize apply." >&2
    exit 1
  fi
  if [[ "${MSSP_APPROVE_BACKEND_BOOTSTRAP:-}" != "$EXPECTED_APPROVAL" ]]; then
    echo "[error] Set MSSP_APPROVE_BACKEND_BOOTSTRAP to the overlay's approval phrase to apply." >&2
    exit 1
  fi
fi

# Backend config must enforce encryption and native locking.
grep -Eq '^[[:space:]]*encrypt[[:space:]]*=[[:space:]]*true' "$BACKEND_HCL" || {
  echo "[error] $BACKEND_HCL must set encrypt = true" >&2; exit 1; }
grep -Eq '^[[:space:]]*use_lockfile[[:space:]]*=[[:space:]]*true' "$BACKEND_HCL" || {
  echo "[error] $BACKEND_HCL must set use_lockfile = true (S3 native locking)" >&2; exit 1; }

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

# Stage 1: create the bucket with LOCAL state (strip the backend "s3" block).
# The bucket cannot hold the state that creates it, so this bootstrap step runs
# locally and the state is migrated in stage 2.
awk '
  /backend[[:space:]]+"s3"[[:space:]]*\{\}/ { next }
  { print }
' "$BOOTSTRAP_DIR/main.tf" > "$STAGING/main.tf"
cp "$BOOTSTRAP_DIR/variables.tf" "$BOOTSTRAP_DIR/outputs.tf" "$STAGING/"
cp "$TFVARS" "$STAGING/bootstrap.tfvars"

terraform -chdir="$STAGING" init -input=false
if [[ "$MODE" == "--plan" ]]; then
  terraform -chdir="$STAGING" plan -input=false -var-file="bootstrap.tfvars"
  echo "[ok] Plan only; no changes applied."
  exit 0
fi
terraform -chdir="$STAGING" apply -input=false -auto-approve -var-file="bootstrap.tfvars"

# Stage 2: migrate the local state into the bucket it just created.
terraform -chdir="$STAGING" init -input=false -force-copy \
  -backend-config="$BACKEND_HCL" -migrate-state
echo "[ok] Backend bootstrapped and state migrated to S3 (native locking)."
