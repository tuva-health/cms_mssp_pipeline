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

# The image must be pinned by digest so a registered task revision resolves to
# exactly one build. PIPELINE_IMAGE comes from the release metadata written by
# build-and-push-image.sh; no mutable ":tag" or latest-discovery is permitted.
require_immutable_image() {
  local image="${PIPELINE_IMAGE:-}"
  if [[ -z "$image" ]]; then
    echo "PIPELINE_IMAGE is required (repository@sha256:<digest>). Set it in $ENV_FILE or the environment." >&2
    exit 1
  fi
  if [[ ! "$image" =~ ^[^@[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
    echo "PIPELINE_IMAGE must be an immutable repository@sha256 digest, got: $image" >&2
    exit 1
  fi
}

# The connector (dbt) image, like the pipeline image, must be pinned by digest
# so a dbt task revision resolves to exactly one build. It is optional: a client
# with no dbt stage never sets it (and no template references it). When set, it
# must be an immutable repository@sha256 digest.
validate_connector_image() {
  local image="${CONNECTOR_IMAGE:-}"
  [[ -z "$image" ]] && return 0
  if [[ ! "$image" =~ ^[^@[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
    echo "CONNECTOR_IMAGE must be an immutable repository@sha256 digest, got: $image" >&2
    exit 1
  fi
  export CONNECTOR_IMAGE
}

resolve_secret_arn() {
  local secret_id="$1"
  aws secretsmanager describe-secret --secret-id "$secret_id" --query ARN --output text
}

recorded_taskdef_arn() {
  # Exact revision recorded at register time; never a mutable "latest" lookup.
  local family="$1"
  local arns_file="$CLIENT_DIR/rendered/task-definition-arns.json"
  [[ -f "$arns_file" ]] || {
    echo "Registered task-definition ARNs not found. Run register-taskdefs first." >&2
    exit 1
  }
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$arns_file" "$family"
}

render_taskdefs() {
  require_immutable_image
  validate_connector_image
  local out_dir="$CLIENT_DIR/rendered"
  mkdir -p "$out_dir"

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

  # Generic, data-driven render: every taskdef-*.json template in the ECS dir
  # is discovered and rendered by the same engine (see scripts/render_taskdefs.py).
  # Templates opt into output-backend augmentation via the "x-mssp-render" marker;
  # unresolved <PLACEHOLDER>s fail the render closed.
  python3 "$ROOT_DIR/scripts/render_taskdefs.py" render "$ROOT_DIR/infra/aws/ecs" "$out_dir"

  echo "Rendered task definitions in: $out_dir"
}

register_taskdefs() {
  local out_dir="$CLIENT_DIR/rendered"
  # Register every rendered taskdef-*.json and record the EXACT registered
  # revision per family in task-definition-arns.json, so activate binds to
  # recorded revisions, never to a mutable "latest" family lookup.
  python3 "$ROOT_DIR/scripts/render_taskdefs.py" register \
    "$out_dir" "$out_dir/task-definition-arns.json"
  echo "Registered all rendered ECS task definitions at exact revisions."
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
    runtime_taskdef_arn="$(recorded_taskdef_arn mssp-pipeline-runtime)"
    echo "Using recorded runtime task definition ARN: $runtime_taskdef_arn"
    terraform -chdir="$tf_dir" apply \
      -auto-approve \
      -var-file="$tfvars" \
      -var="runtime_task_definition_arn=$runtime_taskdef_arn"
  else
    terraform -chdir="$tf_dir" apply -auto-approve -var-file="$tfvars"
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
