"""Generic Terraform substrate contract.

Client-neutral assertions over the parameterized backend/foundation engines and
the sanitized client.example. No account, region, bucket, or client literal is
asserted here -- only the presence of the generic mechanism and the absence of
any client identity in the upstream tree.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TF = ROOT / "infra" / "terraform" / "aws"
EXAMPLE = ROOT / "infra" / "clients" / "client.example"

# Committed subtrees whose contents must stay client-neutral. The guard
# enumerates the git-tracked files under these (not the raw filesystem), so
# untracked cruft (stale ``__pycache__/*.pyc``, build outputs, a stray
# ``release-metadata/``) can't spuriously fail the genericity proof (TUVA-42).
SCANNED_SUBTREES = (
    "infra/terraform/aws",
    "infra/clients/client.example",
    "scripts",
    "infra/aws",
)

# Identity fragments that must never appear in the canonical (upstream) tree.
FORBIDDEN = [
    "vbca",
    "441168071338",
    "VBCA_TUVA",
    "VBCA_MSSP_RUNTIME",
    "evydekl",
    "A5495",
    "TUVA_WH",
    "MSSPDeployer",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_terraform_version_is_pinned() -> None:
    assert (ROOT / ".terraform-version").read_text().strip() == "1.14.8"


def test_bootstrap_module_hardens_the_state_bucket() -> None:
    main = read(TF / "bootstrap" / "main.tf")
    assert 'backend "s3" {}' in main
    assert "aws_s3_bucket_versioning" in main
    assert 'sse_algorithm = "AES256"' in main
    assert "aws_s3_bucket_public_access_block" in main
    assert 'object_ownership = "BucketOwnerEnforced"' in main
    assert "DenyInsecureTransport" in main
    assert 'aws:SecureTransport' in main
    # Native S3 locking: no DynamoDB lock table or config key in the module.
    assert "aws_dynamodb_table" not in main
    assert "dynamodb_table" not in main


def test_bootstrap_account_and_region_are_parameters_not_literals() -> None:
    variables = read(TF / "bootstrap" / "variables.tf")
    # account/region are inputs validated by shape, not pinned to one value.
    assert 'variable "aws_account_id"' in variables
    assert 'variable "aws_region"' in variables
    assert "[0-9]{12}" in variables
    assert not re.search(r'default\s*=\s*"[0-9]{12}"', variables)
    assert 'variable "deployer_role_name"' in variables


def test_foundation_adds_generic_hardening_engines() -> None:
    assert "aws_s3_bucket" in read(TF / "foundation" / "data_bucket.tf")
    ecr = read(TF / "foundation" / "ecr.tf")
    assert 'image_tag_mutability = "IMMUTABLE"' in ecr
    assert "scan_on_push = true" in ecr
    stage_iam = read(TF / "foundation" / "stage_iam.tf")
    assert "ssm:GetParameter" in stage_iam
    variables = read(TF / "foundation" / "variables.tf")
    assert 'variable "allowed_account_ids"' in variables
    assert 'variable "log_retention_days"' in variables
    assert 'variable "data_bucket_name"' in variables


def test_execution_roles_get_least_privilege_readiness_injection() -> None:
    """ECS resolves the readiness-gate container secrets with the task
    EXECUTION role, so the substrate grants ssm:GetParameters on exactly the two
    gate parameters: to its own execution role unconditionally, and to any
    per-stage execution role a client overlay lists by name (no wildcard,
    no literal role name)."""
    stage_iam = read(TF / "foundation" / "stage_iam.tf")
    assert '"ssm:GetParameters"' in stage_iam
    injection = stage_iam[stage_iam.index('"stage_readiness_injection"'):]
    assert "aws_ssm_parameter.bootstrap_complete.arn" in injection
    assert "aws_ssm_parameter.whitelist_confirmed.arn" in injection
    assert '"*"' not in injection
    assert "role   = aws_iam_role.ecs_task_execution.id" in injection
    assert "for_each = toset(var.readiness_execution_role_names)" in injection
    assert not re.search(r'role\s*=\s*"', injection)  # attached by variable, never a literal
    variables = read(TF / "foundation" / "variables.tf")
    assert 'variable "readiness_execution_role_names"' in variables
    assert re.search(r'readiness_execution_role_names"\s*\{[^}]*default\s*=\s*\[\]', variables, re.S)
    example = read(EXAMPLE / "foundation.tfvars.example")
    assert "readiness_execution_role_names" in example


def test_client_example_backends_use_native_locking() -> None:
    for name in (
        "bootstrap.backend.hcl.example",
        "foundation.backend.hcl.example",
        "activate.backend.hcl.example",
    ):
        content = read(EXAMPLE / name)
        assert "use_lockfile = true" in content
        assert "encrypt      = true" in content or "encrypt = true" in content
        assert "dynamodb_table" not in content


def test_client_example_has_bootstrap_scaffolding() -> None:
    assert (EXAMPLE / "bootstrap.tfvars.example").is_file()
    tfvars = read(EXAMPLE / "bootstrap.tfvars.example")
    assert "aws_account_id" in tfvars
    assert "deployer_principal_arns" in tfvars


def git_tracked_files(root: Path, subtrees: Sequence[str]) -> list[Path]:
    """Absolute paths of the git-tracked files under ``subtrees``.

    Uses ``git ls-files`` so only committed/staged files are scanned -- never
    untracked artifacts that happen to sit in the working tree.
    """
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", *subtrees],
        check=True,
        capture_output=True,
        text=True,
    )
    return [root / rel for rel in result.stdout.split("\0") if rel]


def scan_for_client_identity(files: Iterable[Path], root: Path = ROOT) -> list[str]:
    """Return ``path: token`` offenders for any FORBIDDEN literal in ``files``."""
    offenders: list[str] = []
    for path in files:
        if ".terraform" in path.parts or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in FORBIDDEN:
            if token.lower() in text.lower():
                offenders.append(f"{path.relative_to(root)}: {token}")
    return offenders


def test_no_client_identity_in_the_upstream_substrate() -> None:
    tracked = git_tracked_files(ROOT, SCANNED_SUBTREES)
    offenders = scan_for_client_identity(tracked)
    assert not offenders, "client identity leaked upstream: " + "; ".join(offenders)


def test_guard_scans_tracked_files_not_untracked_cruft(tmp_path) -> None:
    """A forbidden token in a TRACKED file is caught; the same token in an
    UNTRACKED file (stale ``.pyc``, build output, ``release-metadata/``) is
    ignored -- so untracked cruft can't spuriously fail the guard (TUVA-42).
    """
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    scripts = tmp_path / "scripts"
    scripts.mkdir()

    # A tracked file carrying a client literal -> must be flagged.
    tracked_leak = scripts / "leak.tf"
    tracked_leak.write_text('name = "vbca-data-bucket"\n', encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "scripts/leak.tf"], check=True
    )

    # Stray UNTRACKED artifacts with forbidden literals -> must be ignored.
    (scripts / "stale.pyc").write_text("evydekl A5495", encoding="utf-8")
    release_meta = tmp_path / "release-metadata"
    release_meta.mkdir()
    (release_meta / "manifest.json").write_text('{"aco": "A5495"}', encoding="utf-8")

    tracked = git_tracked_files(tmp_path, ("scripts", "release-metadata"))
    offenders = scan_for_client_identity(tracked, root=tmp_path)

    # The tracked leak is caught; nothing untracked is.
    assert any("leak.tf" in o for o in offenders), offenders
    assert not any("stale.pyc" in o for o in offenders), offenders
    assert not any("release-metadata" in o for o in offenders), offenders
