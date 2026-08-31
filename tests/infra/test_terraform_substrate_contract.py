"""Generic Terraform substrate contract.

Client-neutral assertions over the parameterized backend/foundation engines and
the sanitized client.example. No account, region, bucket, or client literal is
asserted here -- only the presence of the generic mechanism and the absence of
any client identity in the upstream tree.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TF = ROOT / "infra" / "terraform" / "aws"
EXAMPLE = ROOT / "infra" / "clients" / "client.example"

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


def test_no_client_identity_in_the_upstream_substrate() -> None:
    offenders: list[str] = []
    for base in (TF, EXAMPLE, ROOT / "scripts", ROOT / "infra" / "aws"):
        for path in base.rglob("*"):
            if not path.is_file() or ".terraform" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in FORBIDDEN:
                if token.lower() in text.lower():
                    offenders.append(f"{path.relative_to(ROOT)}: {token}")
    assert not offenders, "client identity leaked upstream: " + "; ".join(offenders)
