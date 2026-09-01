"""Generic ECS taskdef render/register engine contract (TUVA-41).

The deploy engine must render EVERY ``taskdef-*.json`` template discovered in
``infra/aws/ecs/`` (not a hardcoded runtime/bootstrap pair) by substituting one
resolved placeholder->value map, failing closed on any unresolved
``<PLACEHOLDER>``. A template opts into output-backend augmentation (env
overrides + conditional Snowflake secrets/env) via a top-level
``x-mssp-render`` marker that is stripped from rendered output; unmarked
templates are rendered by pure text substitution.

These assertions are client-neutral: synthetic values only, no client literal.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ECS = ROOT / "infra" / "aws" / "ecs"
EXPECTED = Path(__file__).resolve().parent / "fixtures" / "render" / "expected"
MODULE_PATH = ROOT / "scripts" / "render_taskdefs.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("render_taskdefs", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_taskdefs = _load_module()


# Fixed, deterministic env used to produce the committed goldens. Non-secret
# synthetic values only.
BASE_ENV = {
    "ACCOUNT_ID": "111122223333",
    "REGION": "us-east-1",
    "PIPELINE_IMAGE": (
        "111122223333.dkr.ecr.us-east-1.amazonaws.com/mssp-pipeline@sha256:"
        + "1" * 64
    ),
    "ACO_ID": "A9999",
    "FILE_STORE_BUCKET": "example-mssp-bucket",
    "FILE_STORE_PREFIX": "raw/input",
    "OUTPUT_PREFIX": "processed/output",
    "ACOMS_CONFIG_SECRET_ARN": (
        "arn:aws:secretsmanager:us-east-1:111122223333:secret:mssp/acoms-config-AAAAAA"
    ),
    "CMS_API_KEY_SECRET_ARN": (
        "arn:aws:secretsmanager:us-east-1:111122223333:secret:mssp/cms-api-key-BBBBBB"
    ),
    "CMS_API_SECRET_SECRET_ARN": (
        "arn:aws:secretsmanager:us-east-1:111122223333:secret:mssp/cms-api-secret-CCCCCC"
    ),
    "NAT_EIP_OR_EMPTY": "",
}

SNOWFLAKE_ENV = {
    **BASE_ENV,
    "MSSP_OUTPUT_TYPE": "SNOWFLAKE",
    "SNOWFLAKE_USERNAME": "svc_mssp",
    "SNOWFLAKE_ACCOUNT": "acme-org.us-east-1",
    "SNOWFLAKE_DATABASE": "MSSP",
    "SNOWFLAKE_SCHEMA": "RAW_DATA",
    "SNOWFLAKE_COMPUTE_WAREHOUSE": "COMPUTE_WH",
    "SNOWFLAKE_ACCOUNT_ROLE": "ACCOUNTADMIN",
    "SNOWFLAKE_RSA_KEY_SECRET_ARN": (
        "arn:aws:secretsmanager:us-east-1:111122223333:secret:mssp/snowflake/rsa-key-DDDDDD"
    ),
    "SNOWFLAKE_RSA_KEY_PASSPHRASE_SECRET_ARN": (
        "arn:aws:secretsmanager:us-east-1:111122223333:secret:"
        "mssp/snowflake/rsa-key-passphrase-EEEEEE"
    ),
}


def test_runtime_and_bootstrap_render_byte_identical_parquet(tmp_path):
    """The two canonical templates render byte-for-byte as before (PARQUET)."""
    render_taskdefs.render_all(str(ECS), str(tmp_path), {**BASE_ENV, "MSSP_OUTPUT_TYPE": "PARQUET"})

    runtime = (tmp_path / "taskdef-runtime.json").read_text(encoding="utf-8")
    bootstrap = (tmp_path / "taskdef-bootstrap.json").read_text(encoding="utf-8")

    assert runtime == (EXPECTED / "taskdef-runtime.parquet.json").read_text(encoding="utf-8")
    assert bootstrap == (EXPECTED / "taskdef-bootstrap.json").read_text(encoding="utf-8")


def test_runtime_renders_byte_identical_snowflake(tmp_path):
    """The Snowflake augmentation (env + injected secrets) is preserved exactly."""
    render_taskdefs.render_all(str(ECS), str(tmp_path), SNOWFLAKE_ENV)

    runtime = (tmp_path / "taskdef-runtime.json").read_text(encoding="utf-8")
    assert runtime == (EXPECTED / "taskdef-runtime.snowflake.json").read_text(encoding="utf-8")


def test_marker_is_stripped_from_rendered_output(tmp_path):
    render_taskdefs.render_all(str(ECS), str(tmp_path), {**BASE_ENV, "MSSP_OUTPUT_TYPE": "PARQUET"})
    runtime = (tmp_path / "taskdef-runtime.json").read_text(encoding="utf-8")
    assert "x-mssp-render" not in runtime


def _write(dir_: Path, name: str, doc: dict) -> Path:
    path = dir_ / name
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def test_discovers_every_template_not_hardcoded_pair(tmp_path):
    """A dir with three templates renders three outputs (loop is data-driven)."""
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    out = tmp_path / "rendered"
    _write(ecs, "taskdef-bootstrap.json", {"family": "f-boot", "region": "<REGION>"})
    _write(ecs, "taskdef-runtime.json", {"family": "f-run", "region": "<REGION>"})
    _write(ecs, "taskdef-download.json", {"family": "f-dl", "region": "<REGION>"})

    render_taskdefs.render_all(str(ecs), str(out), BASE_ENV)

    rendered = sorted(p.name for p in out.glob("taskdef-*.json"))
    assert rendered == [
        "taskdef-bootstrap.json",
        "taskdef-download.json",
        "taskdef-runtime.json",
    ]


def test_synthetic_marked_template_gets_output_backend_augmentation(tmp_path):
    """A synthetic third taskdef carrying the marker receives the augmentation."""
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    out = tmp_path / "rendered"
    _write(
        ecs,
        "taskdef-stage.json",
        {
            "x-mssp-render": {"augment": "output-backend"},
            "family": "mssp-pipeline-stage",
            "containerDefinitions": [
                {
                    "name": "worker",
                    "image": "<PIPELINE_IMAGE_URI>",
                    "environment": [{"name": "AWS_REGION", "value": "<REGION>"}],
                }
            ],
        },
    )

    render_taskdefs.render_all(str(ecs), str(out), {**BASE_ENV, "MSSP_OUTPUT_TYPE": "PARQUET"})

    doc = json.loads((out / "taskdef-stage.json").read_text(encoding="utf-8"))
    assert "x-mssp-render" not in doc
    env = {e["name"]: e["value"] for e in doc["containerDefinitions"][0]["environment"]}
    assert env["MSSP_OUTPUT_TYPE"] == "PARQUET"
    assert env["MSSP_OUTPUT_LOCATION"] == "s3://example-mssp-bucket/processed/output"
    assert env["MSSP_TEMP_LOCATION"] == "/tmp/mssp-staging"
    assert env["MSSP_DOWNLOAD_MODE"] == "incremental"
    assert env["AWS_REGION"] == "us-east-1"  # placeholder still substituted


def test_synthetic_marked_template_injects_snowflake_secrets(tmp_path):
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    out = tmp_path / "rendered"
    _write(
        ecs,
        "taskdef-stage.json",
        {
            "x-mssp-render": {"augment": "output-backend"},
            "family": "mssp-pipeline-stage",
            "containerDefinitions": [
                {"name": "worker", "image": "<PIPELINE_IMAGE_URI>", "environment": []}
            ],
        },
    )

    render_taskdefs.render_all(str(ecs), str(out), SNOWFLAKE_ENV)

    doc = json.loads((out / "taskdef-stage.json").read_text(encoding="utf-8"))
    container = doc["containerDefinitions"][0]
    env = {e["name"]: e["value"] for e in container["environment"]}
    assert env["MSSP_OUTPUT_TYPE"] == "SNOWFLAKE"
    assert env["SNOWFLAKE_DATABASE"] == "MSSP"
    assert env["SNOWFLAKE_RSA_KEY_PATH"] == "/tmp/snowflake_rsa_key.p8"
    secrets = {s["name"]: s["valueFrom"] for s in container["secrets"]}
    assert secrets["SNOWFLAKE_RSA_KEY"].endswith("rsa-key-DDDDDD")
    assert secrets["SNOWFLAKE_RSA_KEY_PASSPHRASE"].endswith("rsa-key-passphrase-EEEEEE")


def test_unmarked_template_is_pure_substitution(tmp_path):
    """An unmarked template is substituted verbatim -- no augmentation injected."""
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    out = tmp_path / "rendered"
    src = _write(
        ecs,
        "taskdef-plain.json",
        {
            "family": "mssp-plain",
            "containerDefinitions": [
                {"name": "c", "image": "<PIPELINE_IMAGE_URI>", "environment": []}
            ],
        },
    )

    render_taskdefs.render_all(str(ecs), str(out), {**BASE_ENV, "MSSP_OUTPUT_TYPE": "PARQUET"})

    rendered = (out / "taskdef-plain.json").read_text(encoding="utf-8")
    # Pure text substitution: identical to the template with the one placeholder
    # replaced and nothing re-serialized or injected.
    expected = src.read_text(encoding="utf-8").replace(
        "<PIPELINE_IMAGE_URI>", BASE_ENV["PIPELINE_IMAGE"]
    )
    assert rendered == expected
    doc = json.loads(rendered)
    env_names = {e["name"] for e in doc["containerDefinitions"][0]["environment"]}
    assert "MSSP_OUTPUT_TYPE" not in env_names


def test_unresolved_placeholder_fails_closed(tmp_path):
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    out = tmp_path / "rendered"
    _write(ecs, "taskdef-bad.json", {"family": "f", "x": "<TOTALLY_UNKNOWN>"})

    with pytest.raises(SystemExit) as exc:
        render_taskdefs.render_all(str(ecs), str(out), BASE_ENV)
    assert "<TOTALLY_UNKNOWN>" in str(exc.value)
    # Fail closed: nothing partially written for the bad template.
    assert not (out / "taskdef-bad.json").exists()


def test_empty_template_dir_fails_closed(tmp_path):
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    with pytest.raises(SystemExit):
        render_taskdefs.render_all(str(ecs), str(tmp_path / "rendered"), BASE_ENV)


# ---- register seam -------------------------------------------------------

_FAKE_AWS = """#!/usr/bin/env python3
import json, sys
# args: ecs register-task-definition --cli-input-json file://<path> --query ... --output text
args = sys.argv[1:]
path = None
for i, a in enumerate(args):
    if a == "--cli-input-json":
        path = args[i + 1][len("file://"):]
doc = json.load(open(path))
print("arn:aws:ecs:us-east-1:111122223333:task-definition/%s:1" % doc["family"])
"""


def _fake_aws(tmp_path: Path) -> Path:
    fake = tmp_path / "aws"
    fake.write_text(_FAKE_AWS, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake


def test_register_records_family_to_arn_for_every_discovered(tmp_path):
    rendered = tmp_path / "rendered"
    rendered.mkdir()
    _write(rendered, "taskdef-bootstrap.json", {"family": "mssp-pipeline-bootstrap"})
    _write(rendered, "taskdef-runtime.json", {"family": "mssp-pipeline-runtime"})
    _write(rendered, "taskdef-download.json", {"family": "mssp-pipeline-download"})
    arns_out = rendered / "task-definition-arns.json"

    fake = _fake_aws(tmp_path)
    arns = render_taskdefs.register_all(
        str(rendered), str(arns_out), aws_cmd=[os.fspath(fake)]
    )

    assert set(arns) == {
        "mssp-pipeline-bootstrap",
        "mssp-pipeline-runtime",
        "mssp-pipeline-download",
    }
    written = json.loads(arns_out.read_text(encoding="utf-8"))
    assert written == arns
    assert written["mssp-pipeline-download"].endswith(
        "task-definition/mssp-pipeline-download:1"
    )
    # activate binds by the runtime family key, which must be present.
    assert "mssp-pipeline-runtime" in written
