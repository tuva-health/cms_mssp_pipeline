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
import shutil
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


# The canonical templates these byte-identical assertions are about. A client
# fork adds staged taskdefs (download / raw-* / dbt-*) to the real ``ECS`` dir;
# those extra templates fail closed on connector/Snowflake placeholders that a
# bare test env doesn't supply. Copying only the named templates into an
# isolated dir keeps these tests asserting *engine behavior on these templates*
# rather than the canonical dir's exact template count (TUVA-48).
CANONICAL_TASKDEFS = ("taskdef-bootstrap.json", "taskdef-runtime.json")


def _canonical_ecs(tmp_path: Path) -> Path:
    """Copy only the canonical taskdef templates into an isolated ECS dir."""
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    for name in CANONICAL_TASKDEFS:
        shutil.copyfile(ECS / name, ecs / name)
    return ecs


def _write(dir_: Path, name: str, doc: dict) -> Path:
    path = dir_ / name
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def test_runtime_and_bootstrap_render_byte_identical_parquet(tmp_path):
    """The two canonical templates render byte-for-byte to their goldens (PARQUET)."""
    ecs = _canonical_ecs(tmp_path)
    out = tmp_path / "rendered"
    render_taskdefs.render_all(str(ecs), str(out), {**BASE_ENV, "MSSP_OUTPUT_TYPE": "PARQUET"})

    runtime = (out / "taskdef-runtime.json").read_text(encoding="utf-8")
    bootstrap = (out / "taskdef-bootstrap.json").read_text(encoding="utf-8")

    assert runtime == (EXPECTED / "taskdef-runtime.parquet.json").read_text(encoding="utf-8")
    assert bootstrap == (EXPECTED / "taskdef-bootstrap.json").read_text(encoding="utf-8")


def test_runtime_renders_byte_identical_snowflake(tmp_path):
    """The Snowflake augmentation (env + injected secrets) renders exactly to
    its golden -- on the workload container, not the sidecar (TUVA-47)."""
    ecs = _canonical_ecs(tmp_path)
    out = tmp_path / "rendered"
    render_taskdefs.render_all(str(ecs), str(out), SNOWFLAKE_ENV)

    runtime = (out / "taskdef-runtime.json").read_text(encoding="utf-8")
    assert runtime == (EXPECTED / "taskdef-runtime.snowflake.json").read_text(encoding="utf-8")


def test_marker_is_stripped_from_rendered_output(tmp_path):
    ecs = _canonical_ecs(tmp_path)
    out = tmp_path / "rendered"
    render_taskdefs.render_all(str(ecs), str(out), {**BASE_ENV, "MSSP_OUTPUT_TYPE": "PARQUET"})
    runtime = (out / "taskdef-runtime.json").read_text(encoding="utf-8")
    assert "x-mssp-render" not in runtime


def test_byte_identical_assertions_survive_extra_overlay_template(tmp_path):
    """Adding an overlay's staged taskdef must not perturb the canonical render.

    A deploying fork drops extra ``taskdef-*.json`` (with their own
    placeholders + marker) into the ECS dir. As long as the render env is
    complete for everything discovered, the engine renders them all: the
    canonical two stay byte-identical to their goldens and the extra template
    renders too. This proves the tests assert engine behavior, not the exact
    template count in the canonical dir (TUVA-48).
    """
    ecs = _canonical_ecs(tmp_path)
    out = tmp_path / "rendered"
    # A stage-like overlay taskdef with its own placeholders + augment marker,
    # the shape a client fork adds (download / raw-* / dbt-*).
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

    # A complete env for everything discovered -> the engine renders all and
    # fails closed on none.
    render_taskdefs.render_all(str(ecs), str(out), SNOWFLAKE_ENV)

    # The canonical outputs are unchanged by the presence of the extra template.
    assert (out / "taskdef-runtime.json").read_text(encoding="utf-8") == (
        EXPECTED / "taskdef-runtime.snowflake.json"
    ).read_text(encoding="utf-8")
    assert (out / "taskdef-bootstrap.json").read_text(encoding="utf-8") == (
        EXPECTED / "taskdef-bootstrap.json"
    ).read_text(encoding="utf-8")

    # The extra stage template rendered too: augmentation applied, marker and
    # placeholders gone.
    stage = json.loads((out / "taskdef-stage.json").read_text(encoding="utf-8"))
    assert "x-mssp-render" not in stage
    env = {e["name"]: e["value"] for e in stage["containerDefinitions"][0]["environment"]}
    assert env["MSSP_OUTPUT_TYPE"] == "SNOWFLAKE"
    assert env["AWS_REGION"] == "us-east-1"  # placeholder substituted


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


def test_augmentation_lands_on_workload_not_readiness_sidecar(tmp_path):
    """The output-backend augmentation targets the essential *workload*
    container, never the non-essential readiness-gates sidecar (TUVA-47).

    In the canonical runtime template the sidecar is ``containerDefinitions[0]``
    and the workload is ``[1]``; the workload is the container that actually
    needs the ``MSSP_OUTPUT_*`` overrides and the Snowflake env + key secrets.
    """
    ecs = _canonical_ecs(tmp_path)
    out = tmp_path / "rendered"
    render_taskdefs.render_all(str(ecs), str(out), SNOWFLAKE_ENV)

    doc = json.loads((out / "taskdef-runtime.json").read_text(encoding="utf-8"))
    containers = {c["name"]: c for c in doc["containerDefinitions"]}
    sidecar, workload = containers["readiness-gates"], containers["mssp-runtime"]
    assert sidecar["essential"] is False and workload["essential"] is True

    workload_env = {e["name"]: e["value"] for e in workload["environment"]}
    assert workload_env["MSSP_OUTPUT_TYPE"] == "SNOWFLAKE"
    assert workload_env["MSSP_OUTPUT_LOCATION"] == "s3://example-mssp-bucket/processed/output"
    assert workload_env["SNOWFLAKE_ACCOUNT"] == "acme-org.us-east-1"
    assert workload_env["SNOWFLAKE_RSA_KEY_PATH"] == "/tmp/snowflake_rsa_key.p8"
    # Statically declared workload env survives alongside the injected values.
    assert workload_env["MSSP_ACO_ID"] == "A9999"
    workload_secrets = {s["name"]: s["valueFrom"] for s in workload["secrets"]}
    assert workload_secrets["ACOMS_CONFIG_TXT"].endswith("acoms-config-AAAAAA")
    assert workload_secrets["SNOWFLAKE_RSA_KEY"].endswith("rsa-key-DDDDDD")
    assert workload_secrets["SNOWFLAKE_RSA_KEY_PASSPHRASE"].endswith("rsa-key-passphrase-EEEEEE")

    # The sidecar is untouched by the augmentation: only its static env, and
    # only the readiness-gate secrets the template itself declares (TUVA-52) --
    # no Snowflake key material lands on it.
    assert sidecar["environment"] == [{"name": "AWS_REGION", "value": "us-east-1"}]
    assert {s["name"] for s in sidecar["secrets"]} == {
        "MSSP_READINESS_BOOTSTRAP",
        "MSSP_READINESS_WHITELIST",
    }


def test_omitted_essential_defaults_to_workload(tmp_path):
    """ECS treats an omitted ``essential`` as true, so a workload that never
    spells it out is still selected over a non-essential sidecar at index 0."""
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
                {"name": "gate", "image": "<PIPELINE_IMAGE_URI>", "essential": False},
                {"name": "worker", "image": "<PIPELINE_IMAGE_URI>"},
            ],
        },
    )

    render_taskdefs.render_all(str(ecs), str(out), {**BASE_ENV, "MSSP_OUTPUT_TYPE": "PARQUET"})

    doc = json.loads((out / "taskdef-stage.json").read_text(encoding="utf-8"))
    gate, worker = doc["containerDefinitions"]
    assert "environment" not in gate
    worker_env = {e["name"]: e["value"] for e in worker["environment"]}
    assert worker_env["MSSP_OUTPUT_TYPE"] == "PARQUET"


@pytest.mark.parametrize(
    "essentials",
    [
        pytest.param([False], id="none-essential"),
        pytest.param([True, True], id="two-essential"),
    ],
)
def test_marked_template_without_single_workload_fails_closed(tmp_path, essentials):
    """A marked template must have exactly one essential (workload) container
    to augment; zero or several is ambiguous and fails the render closed."""
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
                {"name": f"c{i}", "image": "<PIPELINE_IMAGE_URI>", "essential": e}
                for i, e in enumerate(essentials)
            ],
        },
    )

    with pytest.raises(SystemExit) as exc:
        render_taskdefs.render_all(str(ecs), str(out), {**BASE_ENV, "MSSP_OUTPUT_TYPE": "PARQUET"})
    assert "exactly one essential" in str(exc.value)
    assert not (out / "taskdef-stage.json").exists()


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


# ---- per-stage placeholder support (download / raw-* / dbt-*) ------------

# Extends the Snowflake env with the per-stage placeholders a staged overlay
# (download / raw-* / dbt-*) references. Synthetic, client-neutral values only.
STAGED_ENV = {
    **SNOWFLAKE_ENV,
    "CONNECTOR_IMAGE": (
        "111122223333.dkr.ecr.us-east-1.amazonaws.com/mssp-connector@sha256:"
        + "2" * 64
    ),
    "SNOWFLAKE_DATABASE_DEV": "MSSP_DEV",
    "SNOWFLAKE_DATABASE_PROD": "MSSP",
    "SNOWFLAKE_QUERY_TAG_DEV": "svc-dbt-dev",
    "SNOWFLAKE_QUERY_TAG_PROD": "svc-dbt-prod",
}


def test_per_stage_placeholders_resolve(tmp_path):
    """The engine keeps its documented promise: a client overlay can drop in
    per-stage taskdefs (download / raw-* / dbt-*) and have every per-stage
    placeholder resolved by the same loop -- no code change per stage."""
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    out = tmp_path / "rendered"
    _write(
        ecs,
        "taskdef-download.json",
        {
            "family": "svc-download",
            "executionRoleArn": "<DOWNLOAD_EXECUTION_ROLE_ARN>",
            "taskRoleArn": "<DOWNLOAD_TASK_ROLE_ARN>",
            "containerDefinitions": [
                {
                    "name": "dl",
                    "image": "<PIPELINE_IMAGE_URI>",
                    "environment": [
                        {"name": "MSSP_ACO_ID", "value": "<ACO_ID>"},
                        {"name": "MSSP_FILE_STORE", "value": "<FILE_STORE_URI>"},
                    ],
                }
            ],
        },
    )
    _write(
        ecs,
        "taskdef-raw-dev.json",
        {
            "family": "svc-raw-dev",
            "executionRoleArn": "<SNOWFLAKE_EXECUTION_ROLE_ARN>",
            "taskRoleArn": "<RAW_TASK_ROLE_ARN>",
            "containerDefinitions": [
                {
                    "name": "raw",
                    "image": "<PIPELINE_IMAGE_URI>",
                    "environment": [
                        {"name": "SNOWFLAKE_USERNAME", "value": "<SNOWFLAKE_USERNAME>"},
                        {"name": "SNOWFLAKE_ACCOUNT", "value": "<SNOWFLAKE_ACCOUNT>"},
                        {"name": "SNOWFLAKE_DATABASE", "value": "<SNOWFLAKE_DATABASE_DEV>"},
                        {"name": "SNOWFLAKE_SCHEMA", "value": "<SNOWFLAKE_SCHEMA>"},
                        {"name": "SNOWFLAKE_COMPUTE_WAREHOUSE", "value": "<SNOWFLAKE_COMPUTE_WAREHOUSE>"},
                        {"name": "SNOWFLAKE_ACCOUNT_ROLE", "value": "<SNOWFLAKE_ACCOUNT_ROLE>"},
                    ],
                    "secrets": [
                        {"name": "SNOWFLAKE_RSA_KEY", "valueFrom": "<SNOWFLAKE_RSA_KEY_SECRET_ARN>"},
                        {"name": "SNOWFLAKE_RSA_KEY_PASSPHRASE", "valueFrom": "<SNOWFLAKE_RSA_KEY_PASSPHRASE_SECRET_ARN>"},
                    ],
                }
            ],
        },
    )
    _write(
        ecs,
        "taskdef-dbt-dev.json",
        {
            "family": "svc-dbt-dev",
            "executionRoleArn": "<SNOWFLAKE_EXECUTION_ROLE_ARN>",
            "taskRoleArn": "<DBT_TASK_ROLE_ARN>",
            "containerDefinitions": [
                {
                    "name": "dbt",
                    "image": "<CONNECTOR_IMAGE_URI>",
                    "environment": [
                        {"name": "SNOWFLAKE_ACCOUNT", "value": "<SNOWFLAKE_ACCOUNT>"},
                        {"name": "SNOWFLAKE_QUERY_TAG", "value": "<SNOWFLAKE_QUERY_TAG_DEV>"},
                    ],
                    "command": ["sh", "-lc", "run --database <SNOWFLAKE_DATABASE_DEV>"],
                }
            ],
        },
    )

    render_taskdefs.render_all(str(ecs), str(out), STAGED_ENV)

    # No unresolved placeholder survived in any rendered output.
    import re as _re
    for name in ("taskdef-download.json", "taskdef-raw-dev.json", "taskdef-dbt-dev.json"):
        assert not _re.findall(r"<[^>]+>", (out / name).read_text(encoding="utf-8"))

    dl = json.loads((out / "taskdef-download.json").read_text(encoding="utf-8"))
    assert dl["executionRoleArn"].endswith(":role/mssp-pipeline-download-execution-role")
    assert dl["taskRoleArn"].endswith(":role/mssp-pipeline-download-task-role")

    raw = json.loads((out / "taskdef-raw-dev.json").read_text(encoding="utf-8"))
    assert raw["executionRoleArn"].endswith(":role/mssp-pipeline-snowflake-execution-role")
    assert raw["taskRoleArn"].endswith(":role/mssp-pipeline-raw-task-role")
    renv = {e["name"]: e["value"] for e in raw["containerDefinitions"][0]["environment"]}
    assert renv["SNOWFLAKE_USERNAME"] == "svc_mssp"
    assert renv["SNOWFLAKE_DATABASE"] == "MSSP_DEV"  # dev DB, not prod
    assert renv["SNOWFLAKE_COMPUTE_WAREHOUSE"] == "COMPUTE_WH"
    rsec = {s["name"]: s["valueFrom"] for s in raw["containerDefinitions"][0]["secrets"]}
    assert rsec["SNOWFLAKE_RSA_KEY"].endswith("rsa-key-DDDDDD")

    dbt = json.loads((out / "taskdef-dbt-dev.json").read_text(encoding="utf-8"))
    assert dbt["taskRoleArn"].endswith(":role/mssp-pipeline-dbt-task-role")
    assert dbt["containerDefinitions"][0]["image"].endswith("mssp-connector@sha256:" + "2" * 64)
    denv = {e["name"]: e["value"] for e in dbt["containerDefinitions"][0]["environment"]}
    assert denv["SNOWFLAKE_QUERY_TAG"] == "svc-dbt-dev"  # dev tag, distinct from prod
    assert "--database MSSP_DEV" in " ".join(dbt["containerDefinitions"][0]["command"])


def test_query_tag_dev_and_prod_resolve_distinctly(tmp_path):
    """dev and prod dbt templates must resolve to distinct query tags from the
    same env (the shared-placeholder collision the old renderer avoided)."""
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    out = tmp_path / "rendered"
    _write(ecs, "taskdef-dbt-dev.json", {"family": "d", "tag": "<SNOWFLAKE_QUERY_TAG_DEV>", "db": "<SNOWFLAKE_DATABASE_DEV>"})
    _write(ecs, "taskdef-dbt-prod.json", {"family": "p", "tag": "<SNOWFLAKE_QUERY_TAG_PROD>", "db": "<SNOWFLAKE_DATABASE_PROD>"})

    render_taskdefs.render_all(str(ecs), str(out), STAGED_ENV)

    dev = json.loads((out / "taskdef-dbt-dev.json").read_text(encoding="utf-8"))
    prod = json.loads((out / "taskdef-dbt-prod.json").read_text(encoding="utf-8"))
    assert (dev["tag"], dev["db"]) == ("svc-dbt-dev", "MSSP_DEV")
    assert (prod["tag"], prod["db"]) == ("svc-dbt-prod", "MSSP")


def test_missing_per_stage_snowflake_value_fails_closed(tmp_path):
    """A per-stage Snowflake placeholder absent from the env fails the render
    closed (unresolved <PLACEHOLDER>) rather than baking an empty value."""
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    out = tmp_path / "rendered"
    _write(ecs, "taskdef-raw-dev.json", {"family": "r", "u": "<SNOWFLAKE_USERNAME>"})

    # BASE_ENV carries no Snowflake values -> must fail closed, not render "".
    with pytest.raises(SystemExit) as exc:
        render_taskdefs.render_all(str(ecs), str(out), BASE_ENV)
    assert "<SNOWFLAKE_USERNAME>" in str(exc.value)


def test_missing_connector_image_fails_closed(tmp_path):
    """A dbt template referencing <CONNECTOR_IMAGE_URI> with no CONNECTOR_IMAGE
    set fails the render closed -- the exact reason the connector image is
    resolved conditionally rather than defaulting to an empty string."""
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    out = tmp_path / "rendered"
    _write(
        ecs,
        "taskdef-dbt-dev.json",
        {"family": "d", "containerDefinitions": [{"name": "dbt", "image": "<CONNECTOR_IMAGE_URI>"}]},
    )
    env = {k: v for k, v in STAGED_ENV.items() if k != "CONNECTOR_IMAGE"}
    with pytest.raises(SystemExit) as exc:
        render_taskdefs.render_all(str(ecs), str(out), env)
    assert "<CONNECTOR_IMAGE_URI>" in str(exc.value)


# ---- readiness gate SSM parameter placeholders (TUVA-52) -----------------

READINESS_BOOTSTRAP_ARN = "arn:aws:ssm:us-east-1:111122223333:parameter/mssp/bootstrap_complete"
READINESS_WHITELIST_ARN = "arn:aws:ssm:us-east-1:111122223333:parameter/mssp/whitelist_confirmed"


def _readiness_template(dir_: Path) -> Path:
    """A staged template whose readiness sidecar injects the gates as secrets."""
    return _write(
        dir_,
        "taskdef-download.json",
        {
            "family": "svc-download",
            "containerDefinitions": [
                {
                    "name": "readiness-gates",
                    "image": "<PIPELINE_IMAGE_URI>",
                    "essential": False,
                    "command": ["python", "-m", "mssp_pipeline.readiness", "bootstrap", "whitelist"],
                    "secrets": [
                        {"name": "MSSP_READINESS_BOOTSTRAP", "valueFrom": "<READINESS_BOOTSTRAP_PARAM_ARN>"},
                        {"name": "MSSP_READINESS_WHITELIST", "valueFrom": "<READINESS_WHITELIST_PARAM_ARN>"},
                    ],
                }
            ],
        },
    )


def test_readiness_param_arns_resolve_from_region_account_and_foundation_names(tmp_path):
    """The gate parameter ARNs are pure functions of REGION + ACCOUNT_ID + the
    foundation's fixed parameter names, so every staged template's readiness
    sidecar resolves them without per-stage config."""
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    out = tmp_path / "rendered"
    _readiness_template(ecs)

    render_taskdefs.render_all(str(ecs), str(out), BASE_ENV)

    doc = json.loads((out / "taskdef-download.json").read_text(encoding="utf-8"))
    gate = doc["containerDefinitions"][0]
    secrets = {s["name"]: s["valueFrom"] for s in gate["secrets"]}
    assert secrets == {
        "MSSP_READINESS_BOOTSTRAP": READINESS_BOOTSTRAP_ARN,
        "MSSP_READINESS_WHITELIST": READINESS_WHITELIST_ARN,
    }
    # Checked, not asserted: the gate values are never plain env values.
    assert not [e for e in gate.get("environment", []) if e["name"].startswith("MSSP_READINESS_")]


@pytest.mark.parametrize("missing", ["REGION", "ACCOUNT_ID"])
def test_readiness_param_arns_fail_closed_without_region_or_account(tmp_path, missing):
    """An SSM ARN with an empty region or account is malformed, so the
    placeholders stay unresolved and the render fails closed."""
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    _readiness_template(ecs)
    env = {k: v for k, v in BASE_ENV.items() if k != missing}
    with pytest.raises(SystemExit) as exc:
        render_taskdefs.render_all(str(ecs), str(tmp_path / "out"), env)
    assert "<READINESS_BOOTSTRAP_PARAM_ARN>" in str(exc.value)


def test_render_fails_closed_when_a_readiness_gate_has_no_secret(tmp_path):
    """A readiness container that checks a gate without declaring its secret
    would read "missing" at runtime (the original bug), so the render refuses
    it -- on any template, marker or not."""
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    _write(
        ecs,
        "taskdef-download.json",
        {
            "family": "svc-download",
            "containerDefinitions": [
                {
                    "name": "readiness-gates",
                    "image": "<PIPELINE_IMAGE_URI>",
                    "essential": False,
                    "command": ["python", "-m", "mssp_pipeline.readiness", "bootstrap", "whitelist"],
                    "secrets": [
                        {"name": "MSSP_READINESS_BOOTSTRAP", "valueFrom": "<READINESS_BOOTSTRAP_PARAM_ARN>"},
                    ],
                }
            ],
        },
    )
    with pytest.raises(SystemExit) as exc:
        render_taskdefs.render_all(str(ecs), str(tmp_path / "out"), BASE_ENV)
    assert "MSSP_READINESS_WHITELIST" in str(exc.value)
    assert not (tmp_path / "out" / "taskdef-download.json").exists()


def test_render_fails_closed_on_asserted_readiness_environment(tmp_path):
    """MSSP_READINESS_* as a plain environment value asserts readiness instead
    of checking it (the TUVA-46 hand-injection); the render refuses it."""
    ecs = tmp_path / "ecs"
    ecs.mkdir()
    doc = json.loads(_readiness_template(ecs).read_text(encoding="utf-8"))
    doc["containerDefinitions"][0]["environment"] = [{"name": "MSSP_READINESS_BOOTSTRAP", "value": "true"}]
    _write(ecs, "taskdef-download.json", doc)
    with pytest.raises(SystemExit) as exc:
        render_taskdefs.render_all(str(ecs), str(tmp_path / "out"), BASE_ENV)
    assert "asserts readiness via environment" in str(exc.value)


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
