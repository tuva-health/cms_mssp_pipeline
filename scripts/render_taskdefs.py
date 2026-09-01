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
the rendered output (ECS rejects unknown keys), so a marked template's rendered
bytes match what the previous bespoke per-template renderer produced.

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


# ---- placeholder map -----------------------------------------------------

def _s3_uri(bucket: str, prefix: str) -> str:
    return f"s3://{bucket}" if not prefix else f"s3://{bucket}/{prefix}"


def _role_arn(account_id: str, project_name: str, suffix: str) -> str:
    return f"arn:aws:iam::{account_id}:role/{project_name}-{suffix}"


def build_placeholder_map(env: Mapping[str, str]) -> dict[str, str]:
    """Resolve the single substitution map from the deploy environment.

    Values that a template does not reference are simply never matched; any
    placeholder a template *does* reference but that is absent here is caught
    by the fail-closed check in :func:`render_text`.
    """
    account_id = env.get("ACCOUNT_ID", "")
    project_name = (env.get("PROJECT_NAME", DEFAULT_PROJECT_NAME).strip() or DEFAULT_PROJECT_NAME)
    bucket = env.get("FILE_STORE_BUCKET", "").strip()
    file_store_prefix = env.get("FILE_STORE_PREFIX", "").strip().strip("/")
    output_prefix = env.get("OUTPUT_PREFIX", "").strip().strip("/")
    output_location_override = env.get("MSSP_OUTPUT_LOCATION", "").strip()

    mapping = {
        "<ACCOUNT_ID>": account_id,
        "<REGION>": env.get("REGION", ""),
        "<PIPELINE_IMAGE_URI>": env.get("PIPELINE_IMAGE", ""),
        "<ACO_ID>": env.get("ACO_ID", "").strip(),
        "<TASK_EXECUTION_ROLE_ARN>": _role_arn(account_id, project_name, "ecs-task-execution-role"),
        "<RUNTIME_TASK_ROLE_ARN>": _role_arn(account_id, project_name, "runtime-task-role"),
        "<BOOTSTRAP_TASK_ROLE_ARN>": _role_arn(account_id, project_name, "bootstrap-task-role"),
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

def _augment_output_backend(doc: dict, env: Mapping[str, str], name: str) -> None:
    """Apply the runtime output-backend augmentation to the first container.

    Mirrors the previous bespoke runtime renderer exactly: overrides the
    ``MSSP_OUTPUT_*`` env on ``containerDefinitions[0]`` and, when the backend
    is Snowflake, injects the Snowflake env plus the private-key secret(s)
    that are supplied at render time rather than declared statically.
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

    container = doc["containerDefinitions"][0]
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
