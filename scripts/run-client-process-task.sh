#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/run-client-process-task.sh <client> [release-id] [--database DB] [--schema SCHEMA] [--full-refresh] [--no-wait] [--skip-build] [--skip-deploy]

Examples:
  scripts/run-client-process-task.sh client.example --database ANALYTICS_DB
  scripts/run-client-process-task.sh client.example 2026-01-15 --database ANALYTICS_DB --schema RAW_DATA
  scripts/run-client-process-task.sh client.example --skip-build --skip-deploy --database ANALYTICS_DB

Notes:
  - Builds and pushes an immutable release by default (scripts/build-and-push-image.sh),
    then renders/registers/activates task definitions against that release's
    repository@sha256 digest (PIPELINE_IMAGE) via scripts/deploy-client.sh.
  - Runs a one-off ECS task against the EXACT mssp-pipeline-runtime revision
    recorded in <overlay>/rendered/task-definition-arns.json by register-taskdefs,
    with command override 'mssp-process'. No mutable tag and no "latest revision" lookup.
  - With --skip-build, the digest comes from release-metadata/<release-id>.json
    when a release id is given, otherwise from PIPELINE_IMAGE (client env.sh).
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

RELEASE_ID=""
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

if [[ -z "$DATABASE" ]]; then
  echo "--database is required" >&2
  usage
  exit 1
fi

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

run_process_task() {
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

  overrides_json="$(python3 - <<'PY' "$runtime_container" "$DATABASE" "$SCHEMA" "$FULL_REFRESH"
import json, sys
container, database, schema, full_refresh = sys.argv[1:5]
print(json.dumps({
    "containerOverrides": [
        {
            "name": container,
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
  echo "[info] image: $image"
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

run_process_task
