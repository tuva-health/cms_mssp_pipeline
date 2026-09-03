"""Client-run convenience scripts bind to the immutable-digest deploy engine.

``scripts/deploy-and-smoke-client.sh`` and ``scripts/run-client-process-task.sh``
must start their one-off ECS task against the EXACT ``mssp-pipeline-runtime``
revision recorded by ``register-taskdefs`` in ``task-definition-arns.json``,
running the ``repository@sha256`` image that revision was rendered with. No
mutable tag, no ``describe-task-definition`` "latest" discovery.

The scripts are exercised for real in a staged mini-repo under ``tmp_path``
with fake ``aws`` / ``terraform`` / child-script executables on ``PATH`` that
record every call. Synthetic values only; no client identity.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ("deploy-and-smoke-client.sh", "run-client-process-task.sh")
# Extra args each script needs to reach the run step.
REQUIRED_ARGS = {
    "deploy-and-smoke-client.sh": (),
    "run-client-process-task.sh": ("--database", "EXAMPLE_DB"),
}

REGISTRY = "111122223333.dkr.ecr.us-east-1.amazonaws.com/mssp-pipeline"
DEPLOYED_IMAGE = f"{REGISTRY}@sha256:{'a' * 64}"
ENV_IMAGE = f"{REGISTRY}@sha256:{'b' * 64}"
FRESH_IMAGE = f"{REGISTRY}@sha256:{'c' * 64}"
RECORDED_ARN = "arn:aws:ecs:us-east-1:111122223333:task-definition/mssp-pipeline-runtime:5"
FRESH_ARN = "arn:aws:ecs:us-east-1:111122223333:task-definition/mssp-pipeline-runtime:7"
TASK_ARN = "arn:aws:ecs:us-east-1:111122223333:task/example/0123456789abcdef"

FAKE_AWS = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(["aws", *args]) + "\\n")
if args[:3] == ["configure", "get", "region"]:
    print("us-east-1"); sys.exit(0)
if args[:2] == ["ecs", "describe-task-definition"]:
    sys.stderr.write("mutable task-definition discovery is forbidden\\n"); sys.exit(97)
if args[:2] == ["ecs", "run-task"]:
    print(json.dumps({"tasks": [%(task_arn)r], "failures": []})); sys.exit(0)
if args[:2] == ["ecs", "wait"]:
    sys.exit(0)
if args[:2] == ["ecs", "describe-tasks"]:
    print(json.dumps({"lastStatus": "STOPPED", "stopCode": "EssentialContainerExited",
                      "stoppedReason": "", "containers": [{"name": "mssp-runtime", "exitCode": 0}]}))
    sys.exit(0)
sys.stderr.write("unexpected aws call: %%r\\n" %% (args,)); sys.exit(98)
""" % {"task_arn": TASK_ARN}

FAKE_TERRAFORM = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(["terraform", *args]) + "\\n")
if "init" in args:
    sys.exit(0)
if "output" in args:
    print(json.dumps({
        "effective_ecs_cluster_arn": {"value": "arn:aws:ecs:us-east-1:111122223333:cluster/example"},
        "effective_ecs_subnet_ids": {"value": ["subnet-aaa", "subnet-bbb"]},
        "effective_ecs_security_group_ids": {"value": ["sg-ccc"]},
    }))
    sys.exit(0)
sys.stderr.write("unexpected terraform call: %r\\n" % (args,)); sys.exit(98)
"""

# Stand-in for scripts/build-and-push-image.sh: "pushes" a release and writes
# the release-metadata file the real script emits (image digest + provenance).
FAKE_BUILD = """#!/usr/bin/env bash
set -euo pipefail
printf '%%s\\n' "$(python3 -c 'import json,sys; print(json.dumps(["build-and-push-image.sh", *sys.argv[1:]]))' "$@")" >> "$FAKE_LOG"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$root/release-metadata"
cat > "$root/release-metadata/$2.json" <<EOF
{"image": "%(image)s", "source_commit": "%(commit)s", "release_id": "$2", "dependency_checksum": "%(deps)s"}
EOF
""" % {"image": FRESH_IMAGE, "commit": "d" * 40, "deps": "e" * 64}

# Stand-in for scripts/deploy-client.sh: like the real one it sources the
# overlay env.sh and renders from PIPELINE_IMAGE, then records the registered
# revision in task-definition-arns.json.
FAKE_DEPLOY = """#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
client_dir="$root/infra/clients/$1"
# shellcheck source=/dev/null
source "$client_dir/env.sh"
printf '%%s\\n' "$(python3 -c 'import json,sys; print(json.dumps(["deploy-client.sh", *sys.argv[1:]]))' "$1" "$2" "PIPELINE_IMAGE=${PIPELINE_IMAGE:-}")" >> "$FAKE_LOG"
mkdir -p "$client_dir/rendered"
case "$2" in
  render-taskdefs)
    python3 - "$client_dir/rendered/taskdef-runtime.json" "$PIPELINE_IMAGE" <<'PY'
import json, sys
json.dump({"family": "mssp-pipeline-runtime", "containerDefinitions": [
    {"name": "readiness-gates", "image": sys.argv[2]},
    {"name": "mssp-runtime", "image": sys.argv[2]},
]}, open(sys.argv[1], "w"))
PY
    ;;
  register-taskdefs)
    echo '{"mssp-pipeline-runtime": "%(arn)s"}' > "$client_dir/rendered/task-definition-arns.json"
    ;;
  activate) ;;
esac
""" % {"arn": FRESH_ARN}


def _executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _runtime_taskdef(image: str) -> dict:
    return {
        "family": "mssp-pipeline-runtime",
        "containerDefinitions": [
            {"name": "readiness-gates", "image": image},
            {"name": "mssp-runtime", "image": image},
        ],
    }


class Harness:
    """A staged mini-repo: copies of the scripts under test + a synthetic overlay."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "repo"
        self.scripts = self.root / "scripts"
        self.scripts.mkdir(parents=True)
        for name in (*SCRIPTS, "verify_release_metadata.py"):
            shutil.copy2(ROOT / "scripts" / name, self.scripts / name)
        _executable(self.scripts / "build-and-push-image.sh", FAKE_BUILD)
        _executable(self.scripts / "deploy-client.sh", FAKE_DEPLOY)
        (self.root / "infra" / "terraform" / "aws" / "activate").mkdir(parents=True)

        self.client_dir = self.root / "infra" / "clients" / "example"
        self.rendered = self.client_dir / "rendered"
        self.rendered.mkdir(parents=True)
        self.write_env(overridable=True)
        self.write_rendered(DEPLOYED_IMAGE, RECORDED_ARN)

        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        _executable(self.bin / "aws", FAKE_AWS)
        _executable(self.bin / "terraform", FAKE_TERRAFORM)
        self.log = tmp_path / "calls.log"

    def write_env(self, *, overridable: bool, image: str | None = ENV_IMAGE) -> None:
        lines = ["export AWS_REGION=us-east-1"]
        if image is not None:
            # The overlay contract: an overridable default so a wrapper that just
            # built a release can hand its digest through to deploy-client.sh.
            lines.append(
                f'export PIPELINE_IMAGE="${{PIPELINE_IMAGE:-{image}}}"'
                if overridable
                else f'export PIPELINE_IMAGE="{image}"'
            )
        (self.client_dir / "env.sh").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def write_rendered(self, image: str, arn: str | None) -> None:
        (self.rendered / "taskdef-runtime.json").write_text(
            json.dumps(_runtime_taskdef(image)), encoding="utf-8"
        )
        arns = self.rendered / "task-definition-arns.json"
        if arn is None:
            arns.unlink(missing_ok=True)
        else:
            arns.write_text(json.dumps({"mssp-pipeline-runtime": arn}), encoding="utf-8")

    def write_release_metadata(self, release_id: str, image: str) -> None:
        meta = self.root / "release-metadata"
        meta.mkdir(exist_ok=True)
        (meta / f"{release_id}.json").write_text(
            json.dumps(
                {
                    "image": image,
                    "source_commit": "d" * 40,
                    "release_id": release_id,
                    "dependency_checksum": "e" * 64,
                }
            ),
            encoding="utf-8",
        )

    def run(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "FAKE_LOG": str(self.log),
        }
        env.pop("PIPELINE_IMAGE", None)
        return subprocess.run(
            [str(self.scripts / script), "example", *args, *REQUIRED_ARGS[script]],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def run_task_calls(self) -> list[list[str]]:
        return [c for c in self.calls() if c[:3] == ["aws", "ecs", "run-task"]]

    def deploy_calls(self) -> list[list[str]]:
        return [c for c in self.calls() if c[0] == "deploy-client.sh"]


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def _flag_value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


# ---- static contract -----------------------------------------------------


@pytest.mark.parametrize("script", SCRIPTS)
def test_scripts_have_no_mutable_tag_or_latest_discovery_path(script: str) -> None:
    text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
    assert "IMAGE_TAG" not in text
    assert "describe-task-definition" not in text
    assert "latest_runtime_taskdef_arn" not in text
    assert "latest_taskdef_arn" not in text
    # Bound to the deploy engine's recorded revision + immutable digest.
    assert "task-definition-arns.json" in text
    assert "@sha256:" in text
    assert "PIPELINE_IMAGE" in text
    assert "release-metadata" in text


# ---- run binds the recorded revision -------------------------------------


@pytest.mark.parametrize("script", SCRIPTS)
def test_run_targets_the_recorded_revision_and_its_digest(harness: Harness, script: str) -> None:
    result = harness.run(script, "--skip-build", "--skip-deploy", "--no-wait")
    assert result.returncode == 0, result.stderr

    (run_task,) = harness.run_task_calls()
    assert _flag_value(run_task, "--task-definition") == RECORDED_ARN
    assert not any(c[:3] == ["aws", "ecs", "describe-task-definition"] for c in harness.calls())
    assert DEPLOYED_IMAGE in result.stdout
    assert RECORDED_ARN in result.stdout
    assert TASK_ARN in result.stdout


def test_process_task_keeps_its_command_and_destination_overrides(harness: Harness) -> None:
    result = harness.run(
        "run-client-process-task.sh", "--skip-build", "--skip-deploy", "--no-wait",
        "--schema", "EXAMPLE_SCHEMA", "--full-refresh",
    )
    assert result.returncode == 0, result.stderr
    (run_task,) = harness.run_task_calls()
    overrides = json.loads(_flag_value(run_task, "--overrides"))
    (container,) = overrides["containerOverrides"]
    assert container["name"] == "mssp-runtime"
    assert container["command"] == ["mssp-process"]
    env = {e["name"]: e["value"] for e in container["environment"]}
    assert env == {
        "SNOWFLAKE_DATABASE": "EXAMPLE_DB",
        "SNOWFLAKE_SCHEMA": "EXAMPLE_SCHEMA",
        "MSSP_FULL_REFRESH": "true",
    }


def test_smoke_task_passes_the_command_override(harness: Harness) -> None:
    result = harness.run(
        "deploy-and-smoke-client.sh", "--skip-build", "--skip-deploy", "--no-wait",
        "--", "mssp-validate", "--target", "process", "--strict",
    )
    assert result.returncode == 0, result.stderr
    (run_task,) = harness.run_task_calls()
    overrides = json.loads(_flag_value(run_task, "--overrides"))
    assert overrides["containerOverrides"] == [
        {"name": "mssp-runtime", "command": ["mssp-validate", "--target", "process", "--strict"]}
    ]


# ---- fail closed ---------------------------------------------------------


@pytest.mark.parametrize("script", SCRIPTS)
def test_run_fails_closed_without_recorded_arns(harness: Harness, script: str) -> None:
    harness.write_rendered(DEPLOYED_IMAGE, arn=None)
    result = harness.run(script, "--skip-build", "--skip-deploy", "--no-wait")
    assert result.returncode != 0
    assert "register-taskdefs" in result.stderr
    assert harness.run_task_calls() == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_run_fails_closed_on_a_family_level_arn(harness: Harness, script: str) -> None:
    # A family name (or family-only ARN) is a mutable "latest" reference.
    harness.write_rendered(
        DEPLOYED_IMAGE,
        arn="arn:aws:ecs:us-east-1:111122223333:task-definition/mssp-pipeline-runtime",
    )
    result = harness.run(script, "--skip-build", "--skip-deploy", "--no-wait")
    assert result.returncode != 0
    assert "mssp-pipeline-runtime" in result.stderr
    assert harness.run_task_calls() == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_run_fails_closed_on_a_mutable_rendered_image(harness: Harness, script: str) -> None:
    harness.write_rendered(f"{REGISTRY}:latest", RECORDED_ARN)
    result = harness.run(script, "--skip-build", "--skip-deploy", "--no-wait")
    assert result.returncode != 0
    assert "sha256" in result.stderr
    assert harness.run_task_calls() == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_deploy_without_an_image_fails_before_touching_aws(harness: Harness, script: str) -> None:
    harness.write_env(overridable=True, image=None)
    result = harness.run(script, "--skip-build", "--no-wait")
    assert result.returncode != 0
    assert "PIPELINE_IMAGE" in result.stderr
    assert harness.deploy_calls() == []
    assert harness.run_task_calls() == []


# ---- the build digest flows through deploy into the run -------------------


@pytest.mark.parametrize("script", SCRIPTS)
def test_built_digest_flows_through_deploy_into_the_run(harness: Harness, script: str) -> None:
    result = harness.run(script, "rel-1", "--no-wait")
    assert result.returncode == 0, result.stderr

    build = [c for c in harness.calls() if c[0] == "build-and-push-image.sh"]
    assert build == [["build-and-push-image.sh", "example", "rel-1"]]

    # Every deploy step ran with the freshly built digest, in engine order.
    assert [c[2] for c in harness.deploy_calls()] == ["render-taskdefs", "register-taskdefs", "activate"]
    assert all(c[3] == f"PIPELINE_IMAGE={FRESH_IMAGE}" for c in harness.deploy_calls())

    (run_task,) = harness.run_task_calls()
    assert _flag_value(run_task, "--task-definition") == FRESH_ARN
    assert FRESH_IMAGE in result.stdout
    assert DEPLOYED_IMAGE not in result.stdout
    assert ENV_IMAGE not in result.stdout


@pytest.mark.parametrize("script", SCRIPTS)
def test_skip_build_with_a_release_id_deploys_that_release(harness: Harness, script: str) -> None:
    harness.write_release_metadata("rel-2", FRESH_IMAGE)
    result = harness.run(script, "rel-2", "--skip-build", "--no-wait")
    assert result.returncode == 0, result.stderr
    assert all(c[3] == f"PIPELINE_IMAGE={FRESH_IMAGE}" for c in harness.deploy_calls())
    (run_task,) = harness.run_task_calls()
    assert _flag_value(run_task, "--task-definition") == FRESH_ARN


@pytest.mark.parametrize("script", SCRIPTS)
def test_skip_build_with_an_unknown_release_id_fails(harness: Harness, script: str) -> None:
    result = harness.run(script, "rel-missing", "--skip-build", "--no-wait")
    assert result.returncode != 0
    assert "release-metadata" in result.stderr
    assert harness.deploy_calls() == []
    assert harness.run_task_calls() == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_skip_build_without_a_release_id_deploys_the_overlay_image(harness: Harness, script: str) -> None:
    result = harness.run(script, "--skip-build", "--no-wait")
    assert result.returncode == 0, result.stderr
    assert all(c[3] == f"PIPELINE_IMAGE={ENV_IMAGE}" for c in harness.deploy_calls())
    assert ENV_IMAGE in result.stdout


@pytest.mark.parametrize("script", SCRIPTS)
def test_release_id_with_skip_deploy_must_match_the_deployed_revision(harness: Harness, script: str) -> None:
    # Asking to run release rel-3 while the recorded revision was rendered from
    # a different digest is drift; refuse rather than run the wrong image.
    harness.write_release_metadata("rel-3", FRESH_IMAGE)
    result = harness.run(script, "rel-3", "--skip-build", "--skip-deploy", "--no-wait")
    assert result.returncode != 0
    assert FRESH_IMAGE in result.stderr
    assert DEPLOYED_IMAGE in result.stderr
    assert harness.run_task_calls() == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_overlay_that_clobbers_the_release_image_fails_closed(harness: Harness, script: str) -> None:
    # An env.sh with a non-overridable PIPELINE_IMAGE would make deploy-client.sh
    # render the stale overlay digest instead of the release just built.
    harness.write_env(overridable=False)
    result = harness.run(script, "rel-4", "--no-wait")
    assert result.returncode != 0
    assert "PIPELINE_IMAGE" in result.stderr
    # Caught after render, before any revision is registered or activated.
    assert [c[2] for c in harness.deploy_calls()] == ["render-taskdefs"]
    assert harness.run_task_calls() == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_malformed_release_metadata_fails_closed(harness: Harness, script: str) -> None:
    harness.write_release_metadata("rel-5", f"{REGISTRY}:latest")
    result = harness.run(script, "rel-5", "--skip-build", "--no-wait")
    assert result.returncode != 0
    assert "verification" in result.stderr
    assert harness.deploy_calls() == []
    assert harness.run_task_calls() == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_skip_deploy_without_skip_build_is_rejected_before_building(harness: Harness, script: str) -> None:
    result = harness.run(script, "--skip-deploy", "--no-wait")
    assert result.returncode != 0
    assert "--skip-build" in result.stderr
    assert harness.calls() == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_unknown_option_is_rejected(harness: Harness, script: str) -> None:
    result = harness.run(script, "--bogus", "--skip-build", "--skip-deploy")
    assert result.returncode != 0
    assert "Unknown option: --bogus" in result.stderr
    assert harness.calls() == []


@pytest.mark.parametrize("script", SCRIPTS)
def test_render_newer_than_recorded_arns_fails_closed(harness: Harness, script: str) -> None:
    # A re-render that was never registered: the recorded revision no longer
    # corresponds to the rendered taskdef, so neither can be trusted.
    now = time.time()
    os.utime(harness.rendered / "task-definition-arns.json", (now - 60, now - 60))
    os.utime(harness.rendered / "taskdef-runtime.json", (now, now))
    result = harness.run(script, "--skip-build", "--skip-deploy", "--no-wait")
    assert result.returncode != 0
    assert "register-taskdefs" in result.stderr
    assert harness.run_task_calls() == []
