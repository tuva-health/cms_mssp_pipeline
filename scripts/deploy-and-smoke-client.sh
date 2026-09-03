#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/deploy-and-smoke-client.sh <client> [release-id] [--no-wait] [--skip-build] [--skip-deploy] [--] [command args...]

Examples:
  scripts/deploy-and-smoke-client.sh client.example
  scripts/deploy-and-smoke-client.sh client.example 2026-01-15
  scripts/deploy-and-smoke-client.sh client.example 2026-01-15 -- mssp-validate --target process --strict
  scripts/deploy-and-smoke-client.sh client.example --skip-build --skip-deploy

Notes:
  - Builds and pushes an immutable release by default (scripts/build-and-push-image.sh),
    then renders/registers/activates task definitions against that release's
    repository@sha256 digest (PIPELINE_IMAGE) via scripts/deploy-client.sh.
  - Starts a one-off ECS runtime task against the EXACT mssp-pipeline-runtime
    revision recorded in <overlay>/rendered/task-definition-arns.json by
    register-taskdefs. No mutable tag and no "latest revision" lookup.
  - With --skip-build, the digest comes from release-metadata/<release-id>.json
    when a release id is given, otherwise from PIPELINE_IMAGE (client env.sh).
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

RELEASE_ID=""
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
    -*)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
    *)
      if [[ -z "$RELEASE_ID" ]]; then
        RELEASE_ID="$1"
        shift
      else
        echo "Unexpected argument: $1" >&2
        usage
        exit 1
      fi
      ;;
  esac
done

if [[ "$SKIP_DEPLOY" == "true" && "$SKIP_BUILD" != "true" ]]; then
  echo "--skip-deploy runs the recorded revision as-is, so a build would never be deployed; pass --skip-build too." >&2
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

if [[ -z "$RELEASE_ID" && "$SKIP_BUILD" != "true" ]]; then
  RELEASE_ID="$(date -u +%Y-%m-%d-%H%M%S)"
fi

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
rendered_dir="$CLIENT_DIR/rendered"
arns_file="$rendered_dir/task-definition-arns.json"
runtime_family="mssp-pipeline-runtime"
runtime_container="mssp-runtime"

# Same immutable-image contract as deploy-client.sh: repository@sha256:<digest>.
IMMUTABLE_IMAGE_RE='^[^@[:space:]]+@sha256:[0-9a-f]{64}$'
# An exact registered revision, never a bare family (which ECS resolves to "latest").
TASKDEF_REVISION_ARN_RE='^arn:aws:ecs:[^:]+:[0-9]{12}:task-definition/[^:/]+:[0-9]+$'

require_immutable_image() {
  local label="$1" image="$2"
  if [[ -z "$image" ]]; then
    echo "[error] $label is required (repository@sha256:<digest>). Build a release, pass a release id, or set PIPELINE_IMAGE in $ENV_FILE." >&2
    exit 1
  fi
  if [[ ! "$image" =~ $IMMUTABLE_IMAGE_RE ]]; then
    echo "[error] $label must be an immutable repository@sha256 digest, got: $image" >&2
    exit 1
  fi
}

# The digest build-and-push-image.sh recorded for a release id.
release_metadata_image() {
  local metadata_file="$ROOT_DIR/release-metadata/$1.json"
  if [[ ! -f "$metadata_file" ]]; then
    echo "[error] Release metadata not found: $metadata_file (build the release, or omit the release id to use PIPELINE_IMAGE)." >&2
    exit 1
  fi
  # errexit does not apply inside the caller's $(...); fail explicitly.
  python3 "$ROOT_DIR/scripts/verify_release_metadata.py" "$metadata_file" >/dev/null || {
    echo "[error] Release metadata failed verification: $metadata_file" >&2
    exit 1
  }
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["image"])' "$metadata_file"
}

# Exact revision recorded by register-taskdefs; never a mutable "latest" lookup.
recorded_runtime_taskdef_arn() {
  if [[ ! -f "$arns_file" ]]; then
    echo "[error] Registered task-definition ARNs not found: $arns_file. Run 'scripts/deploy-client.sh $CLIENT register-taskdefs' first." >&2
    exit 1
  fi
  if [[ "$rendered_dir/taskdef-runtime.json" -nt "$arns_file" ]]; then
    echo "[error] $rendered_dir/taskdef-runtime.json is newer than $arns_file: the recorded revision was not registered from the current render. Run 'scripts/deploy-client.sh $CLIENT register-taskdefs' first." >&2
    exit 1
  fi
  local arn
  arn="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))' "$arns_file" "$runtime_family")"
  if [[ ! "$arn" =~ $TASKDEF_REVISION_ARN_RE ]]; then
    echo "[error] $arns_file has no exact registered revision for $runtime_family (got: '${arn:-<missing>}'). Re-run register-taskdefs." >&2
    exit 1
  fi
  echo "$arn"
}

# The image baked into the rendered runtime taskdef that register-taskdefs
# registered, i.e. what the recorded revision actually runs.
rendered_runtime_image() {
  local rendered="$rendered_dir/taskdef-runtime.json"
  if [[ ! -f "$rendered" ]]; then
    echo "[error] Rendered runtime task definition not found: $rendered. Run 'scripts/deploy-client.sh $CLIENT render-taskdefs' first." >&2
    exit 1
  fi
  python3 - "$rendered" "$runtime_container" <<'PY'
import json, sys
path, name = sys.argv[1], sys.argv[2]
images = {c["name"]: c.get("image", "") for c in json.load(open(path)).get("containerDefinitions", [])}
if not images.get(name):
    raise SystemExit(f"container {name} has no image in {path}")
print(images[name])
PY
}

terraform_init_activate() {
  if [[ -f "$activate_backend_hcl" ]]; then
    terraform -chdir="$activate_tf_dir" init -backend-config="$activate_backend_hcl" >/dev/null
  else
    terraform -chdir="$activate_tf_dir" init >/dev/null
  fi
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
  echo "[info] Building and pushing immutable release $RELEASE_ID for $CLIENT"
  "$ROOT_DIR/scripts/build-and-push-image.sh" "$CLIENT" "$RELEASE_ID"
}

deploy_client() {
  echo "[info] Rendering/registering/activating ECS task definitions for $CLIENT"
  echo "[info] PIPELINE_IMAGE=$PIPELINE_IMAGE"
  "$ROOT_DIR/scripts/deploy-client.sh" "$CLIENT" render-taskdefs
  # deploy-client.sh re-sources env.sh; a non-overridable PIPELINE_IMAGE there
  # would have rendered the overlay's stale digest. Catch that before any
  # revision is registered or activated.
  local rendered
  rendered="$(rendered_runtime_image)"
  if [[ "$rendered" != "$PIPELINE_IMAGE" ]]; then
    echo "[error] Rendered runtime image $rendered does not match PIPELINE_IMAGE=$PIPELINE_IMAGE." >&2
    echo "        Make sure $ENV_FILE sets an overridable default: export PIPELINE_IMAGE=\"\${PIPELINE_IMAGE:-...}\"" >&2
    exit 1
  fi
  "$ROOT_DIR/scripts/deploy-client.sh" "$CLIENT" register-taskdefs
  "$ROOT_DIR/scripts/deploy-client.sh" "$CLIENT" activate
}

run_smoke_task() {
  local taskdef_arn image network_json cluster_arn overrides_json run_output task_arn
  taskdef_arn="$(recorded_runtime_taskdef_arn)"
  image="$(rendered_runtime_image)"
  require_immutable_image "Rendered runtime image" "$image"
  if [[ -n "$RELEASE_IMAGE" && "$image" != "$RELEASE_IMAGE" ]]; then
    echo "[error] The recorded revision runs $image, not release image $RELEASE_IMAGE." >&2
    echo "        Deploy that release first (drop --skip-deploy) or omit the release id to run the recorded revision." >&2
    exit 1
  fi

  terraform_init_activate
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
    overrides_json="$(python3 - <<'PY' "$command_json" "$runtime_container"
import json, sys
command = json.loads(sys.argv[1])
print(json.dumps({
    "containerOverrides": [
        {"name": sys.argv[2], "command": command}
    ]
}))
PY
)"
  fi

  echo "[info] Starting one-off ECS runtime task"
  echo "[info] image: $image"
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

echo "[info] Client=$CLIENT release=${RELEASE_ID:-<deployed revision>}"

if [[ "$SKIP_BUILD" != "true" ]]; then
  build_and_push
else
  echo "[info] Skipping image build/push (--skip-build)"
fi

# The digest this invocation stands for. A release id (built now, or earlier)
# pins it from verified release metadata; otherwise the overlay's PIPELINE_IMAGE
# is deployed. Empty only with --skip-deploy and no release id: run whatever
# digest the recorded revision was rendered with.
RELEASE_IMAGE=""
if [[ -n "$RELEASE_ID" ]]; then
  RELEASE_IMAGE="$(release_metadata_image "$RELEASE_ID")"
elif [[ "$SKIP_DEPLOY" != "true" ]]; then
  RELEASE_IMAGE="${PIPELINE_IMAGE:-}"
  require_immutable_image "PIPELINE_IMAGE" "$RELEASE_IMAGE"
fi

if [[ "$SKIP_DEPLOY" != "true" ]]; then
  export PIPELINE_IMAGE="$RELEASE_IMAGE"
  deploy_client
else
  echo "[info] Skipping ECS deploy/activate (--skip-deploy)"
fi

run_smoke_task
