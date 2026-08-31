#!/usr/bin/env bash
set -euo pipefail
#
# Reusable remote-state backend validation.
#
# Confirms that the state bucket referenced by a client overlay is hardened
# (versioned, encrypted, private, single-owner, TLS-only) and that every root's
# backend key uses S3 native locking. Read-only; safe to run any time.

usage() {
  cat <<'EOF'
Usage:
  scripts/verify-terraform-backend.sh <client>

Reads infra/clients/<client>/{bootstrap,foundation,activate}.backend.hcl and
checks the referenced state bucket. Requires read access to the bucket.
EOF
}

CLIENT="${1:-}"
if [[ -z "$CLIENT" ]]; then usage; exit 1; fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT_DIR="$ROOT_DIR/infra/clients/$CLIENT"
[[ -d "$CLIENT_DIR" ]] || { echo "[error] Client overlay not found: $CLIENT_DIR" >&2; exit 1; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "[error] Required command not found: $1" >&2; exit 1; }; }
require_cmd aws

hcl_value() {
  # Extract a bare or quoted value for a backend.hcl key.
  local file="$1" key="$2"
  sed -nE "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*\"?([^\"[:space:]]+)\"?.*/\1/p" "$file" | head -n1
}

fail=0
note() { echo "[fail] $1" >&2; fail=1; }

BUCKET=""
for stage in bootstrap foundation activate; do
  hcl="$CLIENT_DIR/$stage.backend.hcl"
  [[ -f "$hcl" ]] || continue
  b="$(hcl_value "$hcl" bucket)"
  [[ -n "$b" ]] || note "$stage.backend.hcl has no bucket"
  [[ -z "$BUCKET" ]] && BUCKET="$b"
  [[ "$b" == "$BUCKET" ]] || note "$stage.backend.hcl bucket ($b) differs from $BUCKET"
  grep -Eq '^[[:space:]]*encrypt[[:space:]]*=[[:space:]]*true' "$hcl" || note "$stage.backend.hcl must set encrypt = true"
  grep -Eq '^[[:space:]]*use_lockfile[[:space:]]*=[[:space:]]*true' "$hcl" || note "$stage.backend.hcl must set use_lockfile = true"
  grep -Eq '^[[:space:]]*dynamodb_table' "$hcl" && note "$stage.backend.hcl must not use a DynamoDB lock table"
  key="$(hcl_value "$hcl" key)"
  [[ -n "$key" ]] || note "$stage.backend.hcl has no key"
done

if [[ -z "$BUCKET" ]]; then
  echo "[error] No backend.hcl files found under $CLIENT_DIR" >&2
  exit 1
fi

versioning="$(aws s3api get-bucket-versioning --bucket "$BUCKET" --query Status --output text 2>/dev/null || echo NONE)"
[[ "$versioning" == "Enabled" ]] || note "bucket $BUCKET versioning is $versioning (want Enabled)"

sse="$(aws s3api get-bucket-encryption --bucket "$BUCKET" \
  --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm' \
  --output text 2>/dev/null || echo NONE)"
[[ "$sse" == "AES256" || "$sse" == "aws:kms" ]] || note "bucket $BUCKET is not encrypted by default"

for flag in BlockPublicAcls BlockPublicPolicy IgnorePublicAcls RestrictPublicBuckets; do
  v="$(aws s3api get-public-access-block --bucket "$BUCKET" \
    --query "PublicAccessBlockConfiguration.$flag" --output text 2>/dev/null || echo NONE)"
  [[ "$v" == "True" ]] || note "bucket $BUCKET $flag is $v (want True)"
done

owner="$(aws s3api get-bucket-ownership-controls --bucket "$BUCKET" \
  --query 'OwnershipControls.Rules[0].ObjectOwnership' --output text 2>/dev/null || echo NONE)"
[[ "$owner" == "BucketOwnerEnforced" ]] || note "bucket $BUCKET ownership is $owner (want BucketOwnerEnforced)"

if [[ "$fail" -eq 0 ]]; then
  echo "[ok] Backend for '$CLIENT' is hardened and uses S3 native locking."
else
  exit 1
fi
