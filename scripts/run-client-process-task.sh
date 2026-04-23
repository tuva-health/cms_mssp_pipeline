#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/run-client-process-task.sh <client> [tag] [--database DB] [--schema SCHEMA] [--full-refresh] [--no-wait] [--skip-build] [--skip-deploy]

Examples:
  scripts/run-client-process-task.sh vbca --database VBCA_TUVA
  scripts/run-client-process-task.sh vbca 2026-04-18-prod-process --database VBCA_TUVA --schema RAW_DATA
  scripts/run-client-process-task.sh vbca --skip-build --skip-deploy --database VBCA_TUVA

Notes:
  - Reuses the latest mssp-pipeline-runtime task definition.
  - Runs a one-off ECS task with command override 'mssp-process'.
  - Overrides Snowflake destination database/schema for that task only.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

CLIENT="${1:-}"
if [[ -z "$CLIENT" ]]; then
  usage
  exit 1
fi
shift

TAG=""
DATABASE=""
SCHEMA="RAW_DATA"
FULL_REFRESH=false
NO_WAIT=false
SKIP_BUILD=false
SKIP_DEPLOY=false

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --database)
      DATABASE="${2:-}"
      shift 2
      ;;
    --schema)
      SCHEMA="${2:-}"
      shift 2
      ;;
    --full-refresh)
      FULL_REFRESH=true
      shift
      ;;
    --no-wait)
      NO_WAIT=true
      shift
      ;;
    --skip-build)
      SKIP_BUILD=true
      shift
      ;;
    --skip-deploy)
      SKIP_DEPLOY=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      if [[ -z "$TAG" ]]; then
        TAG="$1"
        shift
      else
        echo "Unexpected argument: $1" >&2
        usage
        exit 1
      fi
      ;;
  esac
done

if [[ -z "$DATABASE" ]]; then
  echo "--database is required" >&2
  usage
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT_DIR="$ROOT_DIR/infra/clients/$CLIENT"
[[ -d "$CLIENT_DIR" ]] || { echo "Client overlay not found: $CLIENT_DIR" >&2; exit 1; }

ENV_FILE="$CLIENT_DIR/env.sh"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_FILE"
fi

if [[ -z "$TAG" ]]; then
  TAG="$(date -u +%Y-%m-%d-%H%M%S)"
fi
export IMAGE_TAG="$TAG"

REGION="${AWS_REGION:-${REGION:-}}"
if [[ -z "$REGION" ]]; then
  REGION="$(aws configure get region 2>/dev/null || true)"
fi
if [[ -z "$REGION" ]]; then
  echo "AWS region is not set. Set AWS_REGION in $ENV_FILE or environment." >&2
  exit 1
fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "[error] Required command not found: $1" >&2; exit 1; }
}

require_cmd aws
require_cmd python3
require_cmd terraform

activate_tf_dir="$ROOT_DIR/infra/terraform/aws/activate"
activate_backend_hcl="$CLIENT_DIR/activate.backend.hcl"

terraform_init_activate() {
  if [[ -f "$activate_backend_hcl" ]]; then
    terraform -chdir="$activate_tf_dir" init -backend-config="$activate_backend_hcl" >/dev/null
  else
    terraform -chdir="$activate_tf_dir" init >/dev/null
  fi
}

latest_runtime_taskdef_arn() {
  aws ecs describe-task-definition \
    --task-definition mssp-pipeline-runtime \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text
}

network_json_from_activate_outputs() {
  ROOT_DIR="$ROOT_DIR" python3 - <<'PY'
import json, os, subprocess

root_dir = os.environ["ROOT_DIR"]
tf_dir = os.path.join(root_dir, "infra", "terraform", "aws", "activate")

payload = json.loads(subprocess.check_output([
    "terraform", f"-chdir={tf_dir}", "output", "-json"
], text=True))

print(json.dumps({
    "cluster": payload["effective_ecs_cluster_arn"]["value"],
    "subnets": payload["effective_ecs_subnet_ids"]["value"],
    "securityGroups": payload["effective_ecs_security_group_ids"]["value"],
}))
PY
}

build_and_push() {
  echo "[info] Building and pushing image for $CLIENT with tag $IMAGE_TAG"
  "$ROOT_DIR/scripts/build-and-push-image.sh" "$CLIENT" "$IMAGE_TAG"
}

deploy_client() {
  echo "[info] Rendering/registering/activating ECS task definitions for $CLIENT"
  "$ROOT_DIR/scripts/deploy-client.sh" "$CLIENT" render-taskdefs
  "$ROOT_DIR/scripts/deploy-client.sh" "$CLIENT" register-taskdefs
  "$ROOT_DIR/scripts/deploy-client.sh" "$CLIENT" activate
}

run_process_task() {
  terraform_init_activate

  local taskdef_arn network_json cluster_arn overrides_json run_output task_arn
  taskdef_arn="$(latest_runtime_taskdef_arn)"
  network_json="$(network_json_from_activate_outputs)"
  cluster_arn="$(python3 - <<'PY' "$network_json"
import json, sys
print(json.loads(sys.argv[1])["cluster"])
PY
)"

  overrides_json="$(python3 - <<'PY' "$DATABASE" "$SCHEMA" "$FULL_REFRESH"
import json, sys
database, schema, full_refresh = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({
    "containerOverrides": [
        {
            "name": "mssp-runtime",
            "command": ["mssp-process"],
            "environment": [
                {"name": "SNOWFLAKE_DATABASE", "value": database},
                {"name": "SNOWFLAKE_SCHEMA", "value": schema},
                {"name": "MSSP_FULL_REFRESH", "value": full_refresh.lower()},
            ],
        }
    ]
}))
PY
)"

  echo "[info] Starting one-off ECS process task"
  echo "[info] image tag: $IMAGE_TAG"
  echo "[info] task definition: $taskdef_arn"
  echo "[info] destination: ${DATABASE}.${SCHEMA}"
  echo "[info] full refresh: $FULL_REFRESH"

  run_output="$(aws ecs run-task \
    --cluster "$cluster_arn" \
    --launch-type FARGATE \
    --task-definition "$taskdef_arn" \
    --network-configuration "$(python3 - <<'PY' "$network_json"
import json, sys
payload = json.loads(sys.argv[1])
print(json.dumps({
    "awsvpcConfiguration": {
        "subnets": payload["subnets"],
        "securityGroups": payload["securityGroups"],
        "assignPublicIp": "DISABLED",
    }
}))
PY
)" \
    --overrides "$overrides_json" \
    --query '{tasks: tasks[].taskArn, failures: failures}' \
    --output json)"

  task_arn="$(python3 - <<'PY' "$run_output"
import json, sys
payload = json.loads(sys.argv[1])
if payload.get("failures"):
    raise SystemExit(json.dumps(payload["failures"], indent=2))
tasks = payload.get("tasks") or []
if not tasks:
    raise SystemExit("No ECS task ARN returned from run-task")
print(tasks[0])
PY
)"

  echo "[ok] Started process task: $task_arn"
  echo "[info] CloudWatch logs group: /ecs/mssp-pipeline"

  if [[ "$NO_WAIT" == "true" ]]; then
    echo "[info] Not waiting for task completion (--no-wait)"
    return
  fi

  echo "[info] Waiting for ECS task to stop..."
  aws ecs wait tasks-stopped --cluster "$cluster_arn" --tasks "$task_arn"
  aws ecs describe-tasks \
    --cluster "$cluster_arn" \
    --tasks "$task_arn" \
    --query 'tasks[0].{lastStatus:lastStatus,stopCode:stopCode,stoppedReason:stoppedReason,containers:containers[].{name:name,lastStatus:lastStatus,exitCode:exitCode,reason:reason}}' \
    --output json
}

echo "[info] Client=$CLIENT tag=$IMAGE_TAG"

if [[ "$SKIP_BUILD" != "true" ]]; then
  build_and_push
else
  echo "[info] Skipping image build/push (--skip-build)"
fi

if [[ "$SKIP_DEPLOY" != "true" ]]; then
  deploy_client
else
  echo "[info] Skipping ECS deploy/activate (--skip-deploy)"
fi

run_process_task
