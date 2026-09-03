"""Generic ECS + deployment engine contract.

Asserts the immutable-image and readiness-gate mechanisms on the task
definitions and the deploy engine. Values (registry, account, destinations)
remain overlay-rendered tokens; nothing here is client-specific.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ECS = ROOT / "infra" / "aws" / "ecs"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def taskdef(stage: str) -> dict:
    return json.loads((ECS / f"taskdef-{stage}.json").read_text(encoding="utf-8"))


def containers(stage: str) -> dict[str, dict]:
    return {c["name"]: c for c in taskdef(stage)["containerDefinitions"]}


def test_taskdefs_use_immutable_image_tokens_not_mutable_tags() -> None:
    for stage in ("runtime", "bootstrap"):
        text = (ECS / f"taskdef-{stage}.json").read_text(encoding="utf-8")
        assert "<PIPELINE_IMAGE_URI>" in text
        # No mutable ":<TAG>" image reference remains.
        assert ":<TAG>" not in text
        assert not re.search(r'"image":\s*"[^"]*:\$?\{?TAG', text)


def test_runtime_workload_depends_on_readiness_gate() -> None:
    conts = containers("runtime")
    assert "readiness-gates" in conts
    gate = conts["readiness-gates"]
    assert gate["essential"] is False
    assert gate["command"] == [
        "python",
        "-m",
        "mssp_pipeline.readiness",
        "bootstrap",
        "whitelist",
    ]
    workload = conts["mssp-runtime"]
    assert {
        "containerName": "readiness-gates",
        "condition": "SUCCESS",
    } in workload["dependsOn"]


def test_readiness_gate_values_are_injected_from_ssm_not_asserted() -> None:
    """The gate reads MSSP_READINESS_<GATE> from env; those must arrive as ECS
    secrets resolved from the SSM parameter ARN placeholders, never as plain
    environment values that would assert readiness instead of checking it."""
    gate = containers("runtime")["readiness-gates"]
    secrets = {s["name"]: s["valueFrom"] for s in gate["secrets"]}
    assert secrets == {
        "MSSP_READINESS_BOOTSTRAP": "<READINESS_BOOTSTRAP_PARAM_ARN>",
        "MSSP_READINESS_WHITELIST": "<READINESS_WHITELIST_PARAM_ARN>",
    }
    env_names = {e["name"] for e in gate.get("environment", [])}
    assert not {n for n in env_names if n.startswith("MSSP_READINESS_")}


def test_bootstrap_does_not_depend_on_the_readiness_gate() -> None:
    # Bootstrap is what *sets* the readiness gates, so it must not require them.
    conts = containers("bootstrap")
    assert "readiness-gates" not in conts
    assert "dependsOn" not in conts["mssp-bootstrap"]


def test_deploy_engine_binds_exact_revisions_without_mutable_discovery() -> None:
    deploy = read("scripts/deploy-client.sh")
    assert "require_immutable_image" in deploy
    assert "@sha256:" in deploy
    # The activate step must not resolve a mutable "latest" family.
    assert "latest_taskdef_arn" not in deploy
    assert "recorded_taskdef_arn" in deploy
    assert "task-definition-arns.json" in deploy
    # PIPELINE_IMAGE is the immutable digest source.
    assert "PIPELINE_IMAGE" in deploy


def test_backend_lifecycle_scripts_are_present_and_generic() -> None:
    bootstrap = read("scripts/bootstrap-terraform-backend.sh")
    verify = read("scripts/verify-terraform-backend.sh")
    assert "use_lockfile" in bootstrap
    assert "migrate-state" in bootstrap
    # Approval phrase is supplied by the overlay, not hardcoded.
    assert "BACKEND_BOOTSTRAP_APPROVAL" in bootstrap
    assert "BucketOwnerEnforced" in verify
    assert "use_lockfile" in verify
