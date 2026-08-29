#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/deploy-and-smoke-client.sh <client> [tag] [--no-wait] [--skip-build] [--skip-deploy] [--] [command args...]

Examples:
  scripts/deploy-and-smoke-client.sh client.example
  scripts/deploy-and-smoke-client.sh client.example 2026-01-15
  scripts/deploy-and-smoke-client.sh client.example 2026-01-15 -- mssp-validate --target process --strict
  scripts/deploy-and-smoke-client.sh client.example --skip-build --skip-deploy

Notes:
  - Builds and pushes a fresh image tag by default, then renders/registers/activates taskdefs.
  - Starts a one-off ECS runtime task against the latest active mssp-pipeline-runtime revision.
  - If no command is provided after '--', the task definition default command is used.
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
NO_WAIT=false
SKIP_BUILD=false
SKIP_DEPLOY=false
COMMAND_ARGS=()

while [[ "$#" -gt 0 ]]; do
  case "$1" in
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
    --)
      shift
      COMMAND_ARGS=("$@")
      break
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

json_array_from_args() {
  python3 - "$@" <<'PY'
import json, sys
print(json.dumps(sys.argv[1:]))
PY
}

network_json_from_activate_outputs() {
  ROOT_DIR="$ROOT_DIR" python3 - <<'PY'
import json, os, subprocess

root_dir = os.environ["ROOT_DIR"]
tf_dir = os.path.join(root_dir, "infra", "terraform", "aws", "activate")

payload = json.loads(subprocess.check_output([
    "terraform", f"-chdir={tf_dir}", "output", "-json"
], text=True))

cluster = payload["effective_ecs_cluster_arn"]["value"]
subnets = payload["effective_ecs_subnet_ids"]["value"]
security_groups = payload["effective_ecs_security_group_ids"]["value"]

print(json.dumps({
    "cluster": cluster,
    "subnets": subnets,
    "securityGroups": security_groups,
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

run_smoke_task() {
  terraform_init_activate

  local taskdef_arn network_json cluster_arn overrides_json run_output task_arn
  taskdef_arn="$(latest_runtime_taskdef_arn)"
  network_json="$(network_json_from_activate_outputs)"
  cluster_arn="$(python3 - <<'PY' "$network_json"
import json, sys
print(json.loads(sys.argv[1])["cluster"])
PY
)"

  overrides_json='{}'
  if (( ${#COMMAND_ARGS[@]} > 0 )); then
    local command_json
    command_json="$(json_array_from_args "${COMMAND_ARGS[@]}")"
    overrides_json="$(python3 - <<'PY' "$command_json"
import json, sys
command = json.loads(sys.argv[1])
print(json.dumps({
    "containerOverrides": [
        {"name": "mssp-runtime", "command": command}
    ]
}))
PY
)"
  fi

  echo "[info] Starting one-off ECS runtime task"
  echo "[info] image tag: $IMAGE_TAG"
  echo "[info] task definition: $taskdef_arn"
  if (( ${#COMMAND_ARGS[@]} > 0 )); then
    echo "[info] command override: ${COMMAND_ARGS[*]}"
  else
    echo "[info] command override: <task definition default>"
  fi

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

  echo "[ok] Started smoke task: $task_arn"
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

run_smoke_task
