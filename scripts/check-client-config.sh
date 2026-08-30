#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/check-client-config.sh <client> [--for foundation|activate|render-taskdefs|register-taskdefs|all]

Examples:
  scripts/check-client-config.sh acme
  scripts/check-client-config.sh acme --for foundation
  scripts/check-client-config.sh acme --for activate
EOF
}

CLIENT="${1:-}"
MODE="all"
if [[ -z "$CLIENT" ]]; then
  usage
  exit 1
fi
if [[ "${2:-}" == "--for" ]]; then
  MODE="${3:-}"
fi

case "$MODE" in
  foundation|activate|render-taskdefs|register-taskdefs|all) ;;
  *)
    echo "Invalid --for mode: $MODE" >&2
    usage
    exit 1
    ;;
esac

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT_DIR="$ROOT_DIR/infra/clients/$CLIENT"
[[ -d "$CLIENT_DIR" ]] || { echo "[error] Client overlay not found: $CLIENT_DIR" >&2; exit 1; }

ENV_FILE="$CLIENT_DIR/env.sh"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_FILE"
else
  echo "[warn] Missing $ENV_FILE (allowed, but region/profile/env vars must come from shell)"
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "[error] Required command not found: $1" >&2; exit 1; }
}

require_file() {
  [[ -f "$1" ]] || { echo "[error] Missing file: $1" >&2; exit 1; }
}

tfvar_has_key() {
  local file="$1" key="$2"
  grep -Eq "^[[:space:]]*${key}[[:space:]]*=" "$file"
}

tfvar_string_value() {
  local file="$1" key="$2"
  local line
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" | tail -n 1 || true)"
  line="${line#*=}"
  line="$(echo "$line" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
  line="${line%\"}"
  line="${line#\"}"
  echo "$line"
}

check_tfvars_keys() {
  local file="$1"; shift
  require_file "$file"
  local missing=()
  for key in "$@"; do
    if ! tfvar_has_key "$file" "$key"; then
      missing+=("$key")
    fi
  done
  if (( ${#missing[@]} > 0 )); then
    echo "[error] Missing required keys in $file: ${missing[*]}" >&2
    exit 1
  fi
}

check_tfvars_no_placeholders() {
  local file="$1"; shift
  local bad=()
  local value
  for key in "$@"; do
    value="$(tfvar_string_value "$file" "$key")"
    if [[ "$value" == *"<"*">"* ]]; then
      bad+=("$key=$value")
    fi
  done
  if (( ${#bad[@]} > 0 )); then
    echo "[error] Placeholder values found in $file: ${bad[*]}" >&2
    echo "        Replace <...> template values with real values." >&2
    exit 1
  fi
}

check_aws_auth() {
  require_cmd aws
  if ! aws sts get-caller-identity --output json >/dev/null 2>&1; then
    echo "[error] AWS auth failed. Configure AWS_PROFILE or env credentials." >&2
    exit 1
  fi
  echo "[ok] AWS auth works"
}

check_region() {
  local region="${AWS_REGION:-${REGION:-}}"
  if [[ -z "$region" ]]; then
    region="$(aws configure get region 2>/dev/null || true)"
  fi
  if [[ -z "$region" ]]; then
    echo "[error] AWS region not set (AWS_REGION/REGION or aws configure)." >&2
    exit 1
  fi
  echo "[ok] AWS region=$region"
}

check_render_env() {
  local missing=()
  local placeholders=()
  local output_type="${MSSP_OUTPUT_TYPE:-PARQUET}"
  output_type="$(echo "$output_type" | tr '[:lower:]' '[:upper:]')"
  require_cmd aws
  for name in ACO_ID FILE_STORE_BUCKET; do
    if [[ -z "${!name:-}" ]]; then
      missing+=("$name")
    elif [[ "${!name}" == *"<"*">"* ]]; then
      placeholders+=("$name=${!name}")
    fi
  done
  if (( ${#missing[@]} > 0 )); then
    echo "[error] Missing required env vars for taskdef render: ${missing[*]}" >&2
    echo "        Set them in $ENV_FILE or current shell." >&2
    exit 1
  fi
  if (( ${#placeholders[@]} > 0 )); then
    echo "[error] Placeholder values found in env vars: ${placeholders[*]}" >&2
    echo "        Replace <...> template values with real values." >&2
    exit 1
  fi
  if [[ -n "${PROJECT_NAME:-}" && "${PROJECT_NAME}" == *"<"*">"* ]]; then
    echo "[error] Placeholder value found in env var: PROJECT_NAME=${PROJECT_NAME}" >&2
    exit 1
  fi
  if [[ -n "$output_type" && "$output_type" == *"<"*">"* ]]; then
    echo "[error] Placeholder value found in env var: MSSP_OUTPUT_TYPE=${output_type}" >&2
    exit 1
  fi
  for secret_id in mssp/cms-api-key mssp/cms-api-secret mssp/acoms-config; do
    if ! aws secretsmanager describe-secret --secret-id "$secret_id" >/dev/null 2>&1; then
      echo "[error] Required Secrets Manager secret not found or unreadable: $secret_id" >&2
      exit 1
    fi
  done
  case "$output_type" in
    SNOWFLAKE)
      local required_snowflake_vars=(
        SNOWFLAKE_USERNAME
        SNOWFLAKE_ACCOUNT
        SNOWFLAKE_DATABASE
        SNOWFLAKE_SCHEMA
        SNOWFLAKE_COMPUTE_WAREHOUSE
        SNOWFLAKE_ACCOUNT_ROLE
        SNOWFLAKE_RSA_KEY_SECRET_ID
      )
      local sf_missing=()
      local sf_placeholders=()
      for name in "${required_snowflake_vars[@]}"; do
        if [[ -z "${!name:-}" ]]; then
          sf_missing+=("$name")
        elif [[ "${!name}" == *"<"*">"* ]]; then
          sf_placeholders+=("$name=${!name}")
        fi
      done
      if (( ${#sf_missing[@]} > 0 )); then
        echo "[error] MSSP_OUTPUT_TYPE=SNOWFLAKE requires env vars: ${sf_missing[*]}" >&2
        exit 1
      fi
      if (( ${#sf_placeholders[@]} > 0 )); then
        echo "[error] Placeholder Snowflake env vars found: ${sf_placeholders[*]}" >&2
        exit 1
      fi
      for secret_name in SNOWFLAKE_RSA_KEY_SECRET_ID SNOWFLAKE_RSA_KEY_PASSPHRASE_SECRET_ID; do
        local secret_id="${!secret_name:-}"
        if [[ -n "$secret_id" ]] && ! aws secretsmanager describe-secret --secret-id "$secret_id" >/dev/null 2>&1; then
          echo "[error] Snowflake secret not found or unreadable: ${secret_name}=$secret_id" >&2
          exit 1
        fi
      done
      echo "[ok] Render env vars present for MSSP_OUTPUT_TYPE=SNOWFLAKE"
      ;;
    *)
      echo "[ok] Render env vars present (FILE_STORE_PREFIX/OUTPUT_PREFIX may be empty for bucket root)"
      ;;
  esac
}

check_register_inputs() {
  local out_dir="$CLIENT_DIR/rendered"
  require_file "$out_dir/taskdef-runtime.json"
  require_file "$out_dir/taskdef-bootstrap.json"
  echo "[ok] Rendered taskdefs found in $out_dir"
}

check_activate_remote_state_keys_if_needed() {
  local file="$CLIENT_DIR/activate.tfvars"
  local backend
  backend="$(tfvar_string_value "$file" foundation_state_backend)"
  if [[ "$backend" == "s3" ]]; then
    check_tfvars_keys "$file" foundation_state_s3_bucket foundation_state_s3_key foundation_state_s3_region
    check_tfvars_no_placeholders "$file" foundation_state_s3_bucket foundation_state_s3_key foundation_state_s3_region
  fi
}

check_process_schedule_keys_if_needed() {
  local file="$CLIENT_DIR/activate.tfvars"
  local enabled process_db process_expr
  enabled="$(tfvar_string_value "$file" enable_process_schedule)"
  if [[ "$enabled" == "true" ]]; then
    check_tfvars_keys "$file" process_schedule_expression process_database process_schema
    check_tfvars_no_placeholders "$file" process_schedule_expression process_database process_schema
    process_expr="$(tfvar_string_value "$file" process_schedule_expression)"
    process_db="$(tfvar_string_value "$file" process_database)"
    if [[ -z "$process_expr" || -z "$process_db" ]]; then
      echo "[error] Process schedule is enabled but process_schedule_expression/process_database are blank in $file" >&2
      exit 1
    fi
  fi
}

require_cmd terraform

case "$MODE" in
  foundation)
    check_aws_auth
    check_region
    check_tfvars_keys "$CLIENT_DIR/foundation.tfvars" region runtime_s3_resource_arns
    check_tfvars_no_placeholders "$CLIENT_DIR/foundation.tfvars" region
    ;;
  activate)
    check_aws_auth
    check_region
    check_tfvars_keys "$CLIENT_DIR/activate.tfvars" region schedule_expression
    check_tfvars_no_placeholders "$CLIENT_DIR/activate.tfvars" region schedule_expression
    check_activate_remote_state_keys_if_needed
    check_process_schedule_keys_if_needed
    ;;
  render-taskdefs)
    check_aws_auth
    check_region
    check_render_env
    ;;
  register-taskdefs)
    check_aws_auth
    check_region
    check_register_inputs
    ;;
  all)
    check_aws_auth
    check_region
    check_tfvars_keys "$CLIENT_DIR/foundation.tfvars" region runtime_s3_resource_arns
    check_tfvars_no_placeholders "$CLIENT_DIR/foundation.tfvars" region
    check_tfvars_keys "$CLIENT_DIR/activate.tfvars" region schedule_expression
    check_tfvars_no_placeholders "$CLIENT_DIR/activate.tfvars" region schedule_expression
    check_activate_remote_state_keys_if_needed
    check_process_schedule_keys_if_needed
    check_render_env
    ;;
esac

echo "[ok] Client config validation passed for '$CLIENT' (mode=$MODE)."
