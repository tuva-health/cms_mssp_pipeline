#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/deploy-client.sh <client> [render-taskdefs|register-taskdefs|foundation|activate|all]

Examples:
  scripts/deploy-client.sh acme render-taskdefs
  scripts/deploy-client.sh acme foundation
  scripts/deploy-client.sh acme register-taskdefs
  scripts/deploy-client.sh acme activate
  scripts/deploy-client.sh acme all
EOF
}

CLIENT="${1:-}"
ACTION="${2:-all}"
if [[ -z "$CLIENT" ]]; then
  usage
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT_DIR="$ROOT_DIR/infra/clients/$CLIENT"
if [[ ! -d "$CLIENT_DIR" ]]; then
  echo "Client overlay not found: $CLIENT_DIR" >&2
  exit 1
fi

"$ROOT_DIR/scripts/check-client-config.sh" "$CLIENT" --for "$ACTION"

ENV_FILE="$CLIENT_DIR/env.sh"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_FILE"
fi

REGION="${AWS_REGION:-${REGION:-}}"
if [[ -z "$REGION" ]]; then
  REGION="$(aws configure get region 2>/dev/null || true)"
fi
if [[ -z "$REGION" ]]; then
  echo "AWS region is not set. Set AWS_REGION in $ENV_FILE or environment." >&2
  exit 1
fi

ACCOUNT_ID="${ACCOUNT_ID:-}"
if [[ -z "$ACCOUNT_ID" ]]; then
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
fi

export AWS_REGION="$REGION"
export REGION
export ACCOUNT_ID

resolve_secret_arn() {
  local secret_id="$1"
  aws secretsmanager describe-secret --secret-id "$secret_id" --query ARN --output text
}

latest_taskdef_arn() {
  local family="$1"
  aws ecs describe-task-definition \
    --task-definition "$family" \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text
}

render_taskdefs() {
  local out_dir="$CLIENT_DIR/rendered"
  mkdir -p "$out_dir"

  local runtime_tpl="$ROOT_DIR/infra/aws/ecs/taskdef-runtime.json"
  local bootstrap_tpl="$ROOT_DIR/infra/aws/ecs/taskdef-bootstrap.json"
  local runtime_out="$out_dir/taskdef-runtime.json"
  local bootstrap_out="$out_dir/taskdef-bootstrap.json"

  local cms_api_key_secret_arn cms_api_secret_secret_arn acoms_config_secret_arn
  local output_type snowflake_rsa_key_secret_arn snowflake_rsa_key_passphrase_secret_arn
  cms_api_key_secret_arn="$(resolve_secret_arn mssp/cms-api-key)"
  cms_api_secret_secret_arn="$(resolve_secret_arn mssp/cms-api-secret)"
  acoms_config_secret_arn="$(resolve_secret_arn mssp/acoms-config)"
  output_type="$(echo "${MSSP_OUTPUT_TYPE:-PARQUET}" | tr '[:lower:]' '[:upper:]')"
  snowflake_rsa_key_secret_arn=""
  snowflake_rsa_key_passphrase_secret_arn=""

  if [[ "$output_type" == "SNOWFLAKE" ]]; then
    snowflake_rsa_key_secret_arn="$(resolve_secret_arn "${SNOWFLAKE_RSA_KEY_SECRET_ID}")"
    if [[ -n "${SNOWFLAKE_RSA_KEY_PASSPHRASE_SECRET_ID:-}" ]]; then
      snowflake_rsa_key_passphrase_secret_arn="$(resolve_secret_arn "${SNOWFLAKE_RSA_KEY_PASSPHRASE_SECRET_ID}")"
    fi
  fi

  export CMS_API_KEY_SECRET_ARN="$cms_api_key_secret_arn"
  export CMS_API_SECRET_SECRET_ARN="$cms_api_secret_secret_arn"
  export ACOMS_CONFIG_SECRET_ARN="$acoms_config_secret_arn"
  export SNOWFLAKE_RSA_KEY_SECRET_ARN="$snowflake_rsa_key_secret_arn"
  export SNOWFLAKE_RSA_KEY_PASSPHRASE_SECRET_ARN="$snowflake_rsa_key_passphrase_secret_arn"

  python3 - "$runtime_tpl" "$runtime_out" <<'PY'
import json, os, re, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, 'r', encoding='utf-8').read()

bucket = os.environ.get('FILE_STORE_BUCKET', '').strip()
file_store_prefix = os.environ.get('FILE_STORE_PREFIX', '').strip().strip('/')
output_prefix = os.environ.get('OUTPUT_PREFIX', '').strip().strip('/')
project_name = os.environ.get('PROJECT_NAME', 'mssp-pipeline').strip() or 'mssp-pipeline'
account_id = os.environ.get('ACCOUNT_ID', '')
region = os.environ.get('REGION', '')
output_type = os.environ.get('MSSP_OUTPUT_TYPE', 'PARQUET').strip().upper() or 'PARQUET'
output_location_override = os.environ.get('MSSP_OUTPUT_LOCATION', '').strip()
temp_location = os.environ.get('MSSP_TEMP_LOCATION', '/tmp/mssp-staging').strip() or '/tmp/mssp-staging'
download_mode = os.environ.get('MSSP_DOWNLOAD_MODE', 'incremental').strip() or 'incremental'
aco_id = os.environ.get('ACO_ID', '').strip()

if not bucket:
    raise SystemExit('FILE_STORE_BUCKET is required for runtime taskdef render')

def s3_uri(b: str, p: str) -> str:
    return f"s3://{b}" if not p else f"s3://{b}/{p}"

def role_arn(role_suffix: str) -> str:
    return f"arn:aws:iam::{account_id}:role/{project_name}-{role_suffix}"

repl = {
    '<ACCOUNT_ID>': account_id,
    '<REGION>': region,
    '<TAG>': os.environ.get('IMAGE_TAG', 'latest'),
    '<ACO_ID>': aco_id,
    '<FILE_STORE_URI>': s3_uri(bucket, file_store_prefix),
    '<OUTPUT_URI>': output_location_override or s3_uri(bucket, output_prefix),
    '<TASK_EXECUTION_ROLE_ARN>': role_arn('ecs-task-execution-role'),
    '<RUNTIME_TASK_ROLE_ARN>': role_arn('runtime-task-role'),
    '<ACOMS_CONFIG_SECRET_ARN>': os.environ.get('ACOMS_CONFIG_SECRET_ARN', ''),
}
for k, v in repl.items():
    text = text.replace(k, v)
left = re.findall(r'<[^>]+>', text)
if left:
    raise SystemExit(f'Unresolved placeholders in runtime taskdef: {sorted(set(left))}')

doc = json.loads(text)
container = doc['containerDefinitions'][0]

env_entries = {item['name']: item for item in container.get('environment', [])}
env_entries['MSSP_OUTPUT_TYPE'] = {'name': 'MSSP_OUTPUT_TYPE', 'value': output_type}
env_entries['MSSP_OUTPUT_LOCATION'] = {
    'name': 'MSSP_OUTPUT_LOCATION',
    'value': output_location_override or s3_uri(bucket, output_prefix),
}
env_entries['MSSP_TEMP_LOCATION'] = {'name': 'MSSP_TEMP_LOCATION', 'value': temp_location}
env_entries['MSSP_DOWNLOAD_MODE'] = {'name': 'MSSP_DOWNLOAD_MODE', 'value': download_mode}

def require_env(name: str) -> str:
    value = os.environ.get(name, '').strip()
    if not value:
        raise SystemExit(f'{name} is required for runtime taskdef render when MSSP_OUTPUT_TYPE={output_type}')
    return value

extra_secrets = []
if output_type == 'SNOWFLAKE':
    snowflake_env = {
        'SNOWFLAKE_USERNAME': require_env('SNOWFLAKE_USERNAME'),
        'SNOWFLAKE_ACCOUNT': require_env('SNOWFLAKE_ACCOUNT'),
        'SNOWFLAKE_DATABASE': require_env('SNOWFLAKE_DATABASE'),
        'SNOWFLAKE_SCHEMA': require_env('SNOWFLAKE_SCHEMA'),
        'SNOWFLAKE_COMPUTE_WAREHOUSE': require_env('SNOWFLAKE_COMPUTE_WAREHOUSE'),
        'SNOWFLAKE_ACCOUNT_ROLE': require_env('SNOWFLAKE_ACCOUNT_ROLE'),
    }
    for name, value in snowflake_env.items():
        env_entries[name] = {'name': name, 'value': value}
    env_entries['SNOWFLAKE_RSA_KEY_PATH'] = {
        'name': 'SNOWFLAKE_RSA_KEY_PATH',
        'value': '/tmp/snowflake_rsa_key.p8',
    }
    key_secret_arn = os.environ.get('SNOWFLAKE_RSA_KEY_SECRET_ARN', '').strip()
    if not key_secret_arn:
        raise SystemExit('SNOWFLAKE_RSA_KEY_SECRET_ARN is required for SNOWFLAKE runtime render')
    extra_secrets.append({'name': 'SNOWFLAKE_RSA_KEY_B64', 'valueFrom': key_secret_arn})
    passphrase_secret_arn = os.environ.get('SNOWFLAKE_RSA_KEY_PASSPHRASE_SECRET_ARN', '').strip()
    if passphrase_secret_arn:
        extra_secrets.append({'name': 'SNOWFLAKE_RSA_KEY_PASSPHRASE', 'valueFrom': passphrase_secret_arn})

container['environment'] = list(env_entries.values())
existing_secret_names = {item['name'] for item in container.get('secrets', [])}
for secret in extra_secrets:
    if secret['name'] not in existing_secret_names:
        container.setdefault('secrets', []).append(secret)

with open(dst, 'w', encoding='utf-8') as f:
    json.dump(doc, f, indent=2)
    f.write('\n')
print(dst)
PY

  python3 - "$bootstrap_tpl" "$bootstrap_out" <<'PY'
import os, re, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src, 'r', encoding='utf-8').read()
project_name = os.environ.get('PROJECT_NAME', 'mssp-pipeline').strip() or 'mssp-pipeline'
account_id = os.environ.get('ACCOUNT_ID', '')

def role_arn(role_suffix: str) -> str:
    return f"arn:aws:iam::{account_id}:role/{project_name}-{role_suffix}"

repl = {
    '<ACCOUNT_ID>': account_id,
    '<REGION>': os.environ.get('REGION', ''),
    '<TAG>': os.environ.get('IMAGE_TAG', 'latest'),
    '<NAT_EIP_OR_EMPTY>': os.environ.get('NAT_EIP_OR_EMPTY', ''),
    '<TASK_EXECUTION_ROLE_ARN>': role_arn('ecs-task-execution-role'),
    '<BOOTSTRAP_TASK_ROLE_ARN>': role_arn('bootstrap-task-role'),
    '<CMS_API_KEY_SECRET_ARN>': os.environ.get('CMS_API_KEY_SECRET_ARN', ''),
    '<CMS_API_SECRET_SECRET_ARN>': os.environ.get('CMS_API_SECRET_SECRET_ARN', ''),
}
for k, v in repl.items():
    text = text.replace(k, v)
left = re.findall(r'<[^>]+>', text)
if left:
    raise SystemExit(f'Unresolved placeholders in bootstrap taskdef: {sorted(set(left))}')
open(dst, 'w', encoding='utf-8').write(text)
print(dst)
PY

  echo "Rendered task definitions in: $out_dir"
}

register_taskdefs() {
  local out_dir="$CLIENT_DIR/rendered"
  local runtime_json="$out_dir/taskdef-runtime.json"
  local bootstrap_json="$out_dir/taskdef-bootstrap.json"
  [[ -f "$runtime_json" && -f "$bootstrap_json" ]] || {
    echo "Rendered taskdefs not found. Run render-taskdefs first." >&2
    exit 1
  }
  aws ecs register-task-definition --cli-input-json "file://$bootstrap_json" >/dev/null
  aws ecs register-task-definition --cli-input-json "file://$runtime_json" >/dev/null
  echo "Registered ECS task definitions (bootstrap + runtime)."
}

terraform_apply() {
  local stage="$1"
  local tf_dir="$ROOT_DIR/infra/terraform/aws/$stage"
  local tfvars="$CLIENT_DIR/$stage.tfvars"
  local backend_hcl="$CLIENT_DIR/$stage.backend.hcl"

  [[ -f "$tfvars" ]] || { echo "Missing tfvars: $tfvars" >&2; exit 1; }

  if [[ -f "$backend_hcl" ]]; then
    terraform -chdir="$tf_dir" init -backend-config="$backend_hcl"
  else
    terraform -chdir="$tf_dir" init
  fi

  if [[ "$stage" == "activate" ]]; then
    local runtime_taskdef_arn
    runtime_taskdef_arn="$(latest_taskdef_arn mssp-pipeline-runtime)"
    echo "Using latest runtime task definition ARN: $runtime_taskdef_arn"
    terraform -chdir="$tf_dir" apply \
      -var-file="$tfvars" \
      -var="runtime_task_definition_arn=$runtime_taskdef_arn"
  else
    terraform -chdir="$tf_dir" apply -var-file="$tfvars"
  fi
}

case "$ACTION" in
  render-taskdefs)
    render_taskdefs
    ;;
  register-taskdefs)
    register_taskdefs
    ;;
  foundation)
    terraform_apply foundation
    ;;
  activate)
    terraform_apply activate
    ;;
  all)
    terraform_apply foundation
    render_taskdefs
    register_taskdefs
    terraform_apply activate
    ;;
  *)
    usage
    exit 1
    ;;
esac
