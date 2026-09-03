#!/usr/bin/env python3
"""Generic ECS taskdef render + register engine for ``deploy-client.sh``.

The engine is data-driven: it discovers *every* ``taskdef-*.json`` template in
the ECS template dir and renders each one by substituting a single resolved
placeholder->value map, then fails closed if any ``<PLACEHOLDER>`` remains.
This lets a client overlay drop in per-stage taskdefs (download / raw-* /
dbt-*) and have them rendered/registered by the same loop -- no code change
per stage.

Most templates render by pure text substitution (bootstrap does). A template
that needs the output-backend augmentation -- overriding the ``MSSP_OUTPUT_*``
env and, for Snowflake, injecting the extra env + private-key secrets that are
not expressible as static placeholders -- opts in with a top-level marker::

    "x-mssp-render": {"augment": "output-backend"}

The marker is capability-descriptive and client-neutral. It is stripped from
the rendered output (ECS rejects unknown keys). The augmentation lands on the
template's single *essential* container -- the workload -- so non-essential
sidecars (e.g. ``readiness-gates``) are never handed the backend env/secrets.

Usage:
    render_taskdefs.py render   <ecs_template_dir> <rendered_out_dir>
    render_taskdefs.py register <rendered_dir> <arns_out_json>
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

MARKER_KEY = "x-mssp-render"
AUGMENT_OUTPUT_BACKEND = "output-backend"
PLACEHOLDER_RE = re.compile(r"<[^>]+>")
DEFAULT_PROJECT_NAME = "mssp-pipeline"
TASKDEF_GLOB = "taskdef-*.json"

# Readiness gate -> SSM parameter name. These are the foundation's fixed gate
# parameters (infra/terraform/aws/foundation/main.tf creates them, the
# bootstrap task flips them to "true", stage_iam.tf grants exactly them). The
# readiness sidecar reads each gate from the MSSP_READINESS_<GATE> env var;
# ECS injects the parameter value into that env var as a container secret
# (valueFrom = the parameter ARN), which <READINESS_<GATE>_PARAM_ARN> resolves.
READINESS_GATE_PARAMETERS = {
    "bootstrap": "/mssp/bootstrap_complete",
    "whitelist": "/mssp/whitelist_confirmed",
}


# ---- placeholder map -----------------------------------------------------

def _s3_uri(bucket: str, prefix: str) -> str:
    return f"s3://{bucket}" if not prefix else f"s3://{bucket}/{prefix}"


def _role_arn(account_id: str, project_name: str, suffix: str) -> str:
    return f"arn:aws:iam::{account_id}:role/{project_name}-{suffix}"


def _ssm_parameter_arn(region: str, account_id: str, name: str) -> str:
    # SSM parameter ARNs carry the leading "/" of the name: parameter/mssp/x.
    return f"arn:aws:ssm:{region}:{account_id}:parameter/{name.lstrip('/')}"


def build_placeholder_map(env: Mapping[str, str]) -> dict[str, str]:
    """Resolve the single substitution map from the deploy environment.

    Values that a template does not reference are simply never matched; any
    placeholder a template *does* reference but that is absent here is caught
    by the fail-closed check in :func:`render_text`.
    """
    account_id = env.get("ACCOUNT_ID", "").strip()
    region = env.get("REGION", "").strip()
    project_name = (env.get("PROJECT_NAME", DEFAULT_PROJECT_NAME).strip() or DEFAULT_PROJECT_NAME)
    bucket = env.get("FILE_STORE_BUCKET", "").strip()
    file_store_prefix = env.get("FILE_STORE_PREFIX", "").strip().strip("/")
    output_prefix = env.get("OUTPUT_PREFIX", "").strip().strip("/")
    output_location_override = env.get("MSSP_OUTPUT_LOCATION", "").strip()

    mapping = {
        "<ACCOUNT_ID>": account_id,
        "<REGION>": region,
        # Resolved unconditionally: every template needs the pipeline image and
        # deploy-client.sh's require_immutable_image guard hard-fails before
        # render if it is unset. The connector image, needed only by dbt stages,
        # is instead resolved conditionally below so it fails closed when a dbt
        # template references it with no image set.
        "<PIPELINE_IMAGE_URI>": env.get("PIPELINE_IMAGE", ""),
        "<ACO_ID>": env.get("ACO_ID", "").strip(),
        # Role ARNs are pure functions of account + project + a per-purpose
        # suffix, so every stage's roles resolve without per-stage config. This
        # is what lets a client overlay drop in per-stage taskdefs (download /
        # raw-* / dbt-*) and have them rendered by the same loop.
        "<TASK_EXECUTION_ROLE_ARN>": _role_arn(account_id, project_name, "ecs-task-execution-role"),
        "<RUNTIME_TASK_ROLE_ARN>": _role_arn(account_id, project_name, "runtime-task-role"),
        "<BOOTSTRAP_TASK_ROLE_ARN>": _role_arn(account_id, project_name, "bootstrap-task-role"),
        "<DOWNLOAD_EXECUTION_ROLE_ARN>": _role_arn(account_id, project_name, "download-execution-role"),
        "<DOWNLOAD_TASK_ROLE_ARN>": _role_arn(account_id, project_name, "download-task-role"),
        "<RAW_TASK_ROLE_ARN>": _role_arn(account_id, project_name, "raw-task-role"),
        "<DBT_TASK_ROLE_ARN>": _role_arn(account_id, project_name, "dbt-task-role"),
        "<SNOWFLAKE_EXECUTION_ROLE_ARN>": _role_arn(account_id, project_name, "snowflake-execution-role"),
        "<ACOMS_CONFIG_SECRET_ARN>": env.get("ACOMS_CONFIG_SECRET_ARN", ""),
        "<CMS_API_KEY_SECRET_ARN>": env.get("CMS_API_KEY_SECRET_ARN", ""),
        "<CMS_API_SECRET_SECRET_ARN>": env.get("CMS_API_SECRET_SECRET_ARN", ""),
        "<NAT_EIP_OR_EMPTY>": env.get("NAT_EIP_OR_EMPTY", ""),
    }
    # S3 destination URIs require a bucket. When absent, leave the placeholders
    # unresolved so any template that references them fails closed rather than
    # rendering a malformed ``s3://`` value.
    if bucket:
        mapping["<FILE_STORE_URI>"] = _s3_uri(bucket, file_store_prefix)
        mapping["<OUTPUT_URI>"] = output_location_override or _s3_uri(bucket, output_prefix)

    # Per-stage Snowflake placeholders (raw-* / dbt-* templates). Each is added
    # only when supplied so a missing value fails the render closed -- an
    # unresolved <PLACEHOLDER> -- rather than baking an empty/invalid taskdef,
    # the same fail-closed rationale used for the S3 URIs above. Database and
    # query tag are per-environment (DEV/PROD) so the dev and prod dbt/raw
    # templates resolve to distinct values from one env, without a shared
    # placeholder collapsing them.
    per_stage = {
        "<CONNECTOR_IMAGE_URI>": env.get("CONNECTOR_IMAGE", "").strip(),
        "<SNOWFLAKE_USERNAME>": env.get("SNOWFLAKE_USERNAME", "").strip(),
        "<SNOWFLAKE_ACCOUNT>": env.get("SNOWFLAKE_ACCOUNT", "").strip(),
        "<SNOWFLAKE_SCHEMA>": env.get("SNOWFLAKE_SCHEMA", "").strip(),
        "<SNOWFLAKE_COMPUTE_WAREHOUSE>": env.get("SNOWFLAKE_COMPUTE_WAREHOUSE", "").strip(),
        "<SNOWFLAKE_ACCOUNT_ROLE>": env.get("SNOWFLAKE_ACCOUNT_ROLE", "").strip(),
        "<SNOWFLAKE_DATABASE_DEV>": env.get("SNOWFLAKE_DATABASE_DEV", "").strip(),
        "<SNOWFLAKE_DATABASE_PROD>": env.get("SNOWFLAKE_DATABASE_PROD", "").strip(),
        "<SNOWFLAKE_QUERY_TAG_DEV>": env.get("SNOWFLAKE_QUERY_TAG_DEV", "").strip(),
        "<SNOWFLAKE_QUERY_TAG_PROD>": env.get("SNOWFLAKE_QUERY_TAG_PROD", "").strip(),
        "<SNOWFLAKE_RSA_KEY_SECRET_ARN>": env.get("SNOWFLAKE_RSA_KEY_SECRET_ARN", "").strip(),
        "<SNOWFLAKE_RSA_KEY_PASSPHRASE_SECRET_ARN>": env.get(
            "SNOWFLAKE_RSA_KEY_PASSPHRASE_SECRET_ARN", ""
        ).strip(),
    }
    mapping.update({key: value for key, value in per_stage.items() if value})

    # Readiness gate parameter ARNs, injected into the readiness sidecar as ECS
    # container secrets. They are pure functions of region + account + the
    # fixed gate parameter names, so every staged template resolves them
    # without per-stage config -- but only when region and account are known:
    # an ARN with an empty region or account is malformed, so leave the
    # placeholders unresolved (fail closed) rather than bake one.
    if region and account_id:
        for gate, name in READINESS_GATE_PARAMETERS.items():
            placeholder = f"<READINESS_{gate.upper()}_PARAM_ARN>"
            mapping[placeholder] = _ssm_parameter_arn(region, account_id, name)
    return mapping


def render_text(template_text: str, mapping: Mapping[str, str], name: str) -> str:
    text = template_text
    for key, value in mapping.items():
        text = text.replace(key, value)
    left = PLACEHOLDER_RE.findall(text)
    if left:
        raise SystemExit(f"Unresolved placeholders in {name}: {sorted(set(left))}")
    return text


# ---- output-backend augmentation ----------------------------------------

def _workload_container(doc: dict, name: str) -> dict:
    """Return the single *essential* container -- the workload -- of ``doc``.

    A taskdef may carry non-essential sidecars (the ``readiness-gates`` gate
    in the canonical runtime template sits at ``containerDefinitions[0]``),
    so the workload is selected by ``essential``, never by position. ECS
    treats an omitted ``essential`` as ``true``. Zero or several essential
    containers is ambiguous (which one should hold the backend secrets?), so
    it fails closed rather than guessing; a template that legitimately needs
    several essential containers should say which is the workload explicitly
    (e.g. a future ``target`` field on the marker) rather than rely on order.
    """
    candidates = [
        c for c in doc.get("containerDefinitions", []) if c.get("essential", True)
    ]
    if len(candidates) != 1:
        found = [c.get("name", "?") for c in candidates]
        raise SystemExit(
            f"output-backend render of {name} needs exactly one essential "
            f"(workload) container to augment; found {len(found)}: {found}"
        )
    return candidates[0]


def _augment_output_backend(doc: dict, env: Mapping[str, str], name: str) -> None:
    """Apply the runtime output-backend augmentation to the workload container.

    Overrides the ``MSSP_OUTPUT_*`` env on the essential (workload) container
    and, when the backend is Snowflake, injects the Snowflake env plus the
    private-key secret(s) that are supplied at render time rather than
    declared statically. Non-essential sidecars are left untouched (TUVA-47).
    """
    bucket = env.get("FILE_STORE_BUCKET", "").strip()
    output_prefix = env.get("OUTPUT_PREFIX", "").strip().strip("/")
    output_type = (env.get("MSSP_OUTPUT_TYPE", "PARQUET").strip().upper() or "PARQUET")
    output_location_override = env.get("MSSP_OUTPUT_LOCATION", "").strip()
    temp_location = env.get("MSSP_TEMP_LOCATION", "/tmp/mssp-staging").strip() or "/tmp/mssp-staging"
    download_mode = env.get("MSSP_DOWNLOAD_MODE", "incremental").strip() or "incremental"

    if not bucket:
        raise SystemExit(f"FILE_STORE_BUCKET is required for output-backend render of {name}")
    output_location = output_location_override or _s3_uri(bucket, output_prefix)

    container = _workload_container(doc, name)
    env_entries = {item["name"]: item for item in container.get("environment", [])}
    env_entries["MSSP_OUTPUT_TYPE"] = {"name": "MSSP_OUTPUT_TYPE", "value": output_type}
    env_entries["MSSP_OUTPUT_LOCATION"] = {"name": "MSSP_OUTPUT_LOCATION", "value": output_location}
    env_entries["MSSP_TEMP_LOCATION"] = {"name": "MSSP_TEMP_LOCATION", "value": temp_location}
    env_entries["MSSP_DOWNLOAD_MODE"] = {"name": "MSSP_DOWNLOAD_MODE", "value": download_mode}

    extra_secrets: list[dict] = []
    if output_type == "SNOWFLAKE":
        def require_env(var: str) -> str:
            value = env.get(var, "").strip()
            if not value:
                raise SystemExit(
                    f"{var} is required for output-backend render of {name} "
                    f"when MSSP_OUTPUT_TYPE={output_type}"
                )
            return value

        snowflake_env = {
            "SNOWFLAKE_USERNAME": require_env("SNOWFLAKE_USERNAME"),
            "SNOWFLAKE_ACCOUNT": require_env("SNOWFLAKE_ACCOUNT"),
            "SNOWFLAKE_DATABASE": require_env("SNOWFLAKE_DATABASE"),
            "SNOWFLAKE_SCHEMA": require_env("SNOWFLAKE_SCHEMA"),
            "SNOWFLAKE_COMPUTE_WAREHOUSE": require_env("SNOWFLAKE_COMPUTE_WAREHOUSE"),
            "SNOWFLAKE_ACCOUNT_ROLE": require_env("SNOWFLAKE_ACCOUNT_ROLE"),
        }
        for var, value in snowflake_env.items():
            env_entries[var] = {"name": var, "value": value}
        env_entries["SNOWFLAKE_RSA_KEY_PATH"] = {
            "name": "SNOWFLAKE_RSA_KEY_PATH",
            "value": "/tmp/snowflake_rsa_key.p8",
        }
        key_secret_arn = env.get("SNOWFLAKE_RSA_KEY_SECRET_ARN", "").strip()
        if not key_secret_arn:
            raise SystemExit(
                f"SNOWFLAKE_RSA_KEY_SECRET_ARN is required for SNOWFLAKE render of {name}"
            )
        extra_secrets.append({"name": "SNOWFLAKE_RSA_KEY", "valueFrom": key_secret_arn})
        passphrase_secret_arn = env.get("SNOWFLAKE_RSA_KEY_PASSPHRASE_SECRET_ARN", "").strip()
        if passphrase_secret_arn:
            extra_secrets.append(
                {"name": "SNOWFLAKE_RSA_KEY_PASSPHRASE", "valueFrom": passphrase_secret_arn}
            )

    container["environment"] = list(env_entries.values())
    existing_secret_names = {item["name"] for item in container.get("secrets", [])}
    for secret in extra_secrets:
        if secret["name"] not in existing_secret_names:
            container.setdefault("secrets", []).append(secret)


_AUGMENTERS = {AUGMENT_OUTPUT_BACKEND: _augment_output_backend}


# ---- render --------------------------------------------------------------

def render_template(path: Path, mapping: Mapping[str, str], env: Mapping[str, str]) -> str:
    """Render one template to its final ECS ``register-task-definition`` JSON."""
    raw = path.read_text(encoding="utf-8")
    # Templates are valid JSON even before substitution (placeholders live
    # inside string values), so the marker can be read up front.
    marker = json.loads(raw).get(MARKER_KEY)

    substituted = render_text(raw, mapping, path.name)  # fail-closed

    if marker is None:
        # Pure substitution -- emit verbatim, no re-serialization.
        return substituted

    augment = marker.get("augment") if isinstance(marker, dict) else None
    augmenter = _AUGMENTERS.get(augment)
    if augmenter is None:
        raise SystemExit(f"Unknown {MARKER_KEY} augment in {path.name}: {marker!r}")

    doc = json.loads(substituted)
    augmenter(doc, env, path.name)
    doc.pop(MARKER_KEY, None)  # strip marker; ECS rejects unknown keys
    return json.dumps(doc, indent=2) + "\n"


def discover_templates(ecs_dir: str | Path) -> list[Path]:
    return sorted(Path(ecs_dir).glob(TASKDEF_GLOB))


def render_all(ecs_dir: str | Path, out_dir: str | Path, env: Mapping[str, str]) -> list[Path]:
    """Render every discovered template into ``out_dir``. Fails closed."""
    templates = discover_templates(ecs_dir)
    if not templates:
        raise SystemExit(f"No {TASKDEF_GLOB} templates found in {ecs_dir}")

    mapping = build_placeholder_map(env)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Render all (may fail closed) before writing any, so a bad template does
    # not leave a partially-rendered dir.
    rendered = [(tpl.name, render_template(tpl, mapping, env)) for tpl in templates]
    written: list[Path] = []
    for name, text in rendered:
        dst = out / name
        dst.write_text(text, encoding="utf-8")
        written.append(dst)
    return written


# ---- register ------------------------------------------------------------

def _family_of(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["family"]


def register_all(
    rendered_dir: str | Path,
    arns_out: str | Path,
    aws_cmd: Sequence[str] = ("aws",),
) -> dict[str, str]:
    """Register every rendered taskdef and record its exact family->ARN.

    Records the precise registered revision for each discovered family so
    ``activate`` binds to it, never to a mutable "latest" family lookup.
    """
    rendered = discover_templates(rendered_dir)
    if not rendered:
        raise SystemExit(f"No rendered {TASKDEF_GLOB} found in {rendered_dir}. Run render first.")

    arns: dict[str, str] = {}
    for path in rendered:
        family = _family_of(path)
        result = subprocess.run(
            [
                *aws_cmd,
                "ecs",
                "register-task-definition",
                "--cli-input-json",
                f"file://{path}",
                "--query",
                "taskDefinition.taskDefinitionArn",
                "--output",
                "text",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        arns[family] = result.stdout.strip()

    Path(arns_out).write_text(json.dumps(arns, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return arns


# ---- cli -----------------------------------------------------------------

def main(argv: Sequence[str]) -> int:
    if not argv:
        raise SystemExit("usage: render_taskdefs.py {render|register} ...")
    command, rest = argv[0], argv[1:]
    if command == "render":
        if len(rest) != 2:
            raise SystemExit("usage: render_taskdefs.py render <ecs_dir> <out_dir>")
        written = render_all(rest[0], rest[1], os.environ)
        for path in written:
            print(path)
    elif command == "register":
        if len(rest) != 2:
            raise SystemExit("usage: render_taskdefs.py register <rendered_dir> <arns_out>")
        arns = register_all(rest[0], rest[1])
        for family, arn in sorted(arns.items()):
            print(f"{family} {arn}")
    else:
        raise SystemExit(f"unknown command: {command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
