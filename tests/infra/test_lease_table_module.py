"""Contract for the reusable lease-table Terraform module.

Client-neutral assertions over the generic lock-table module: it provisions an
on-demand DynamoDB table with a ``lease_name`` hash key and a TTL attribute,
takes table name and region as inputs (no client literal), and hardcodes no
account/region/client value. The concrete overlay instantiates it with real
values; that ``terraform apply`` is a later cloud step, not this module.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "infra" / "terraform" / "aws" / "modules" / "lease-table"


def read(name: str) -> str:
    return (MODULE / name).read_text(encoding="utf-8")


def test_module_files_exist() -> None:
    for name in ("main.tf", "variables.tf", "outputs.tf"):
        assert (MODULE / name).is_file(), f"missing {name}"


def test_provisions_on_demand_lock_table_with_lease_name_hash_key() -> None:
    main = read("main.tf")
    assert "aws_dynamodb_table" in main
    assert 'billing_mode = "PAY_PER_REQUEST"' in main
    assert "hash_key" in main
    # The hash key attribute is a string keyed on lease_name.
    assert "lease_name" in main
    assert 'type = "S"' in main


def test_declares_a_ttl_attribute() -> None:
    main = read("main.tf")
    assert re.search(r"ttl\s*{", main), "table must declare a ttl block"
    assert "attribute_name" in main
    assert "enabled" in main


def test_table_name_and_region_are_inputs_not_literals() -> None:
    variables = read("variables.tf")
    assert 'variable "table_name"' in variables
    assert 'variable "region"' in variables
    main = read("main.tf")
    # Table name comes from the input variable, never a baked literal.
    assert "var.table_name" in main
    # No hardcoded 12-digit account id anywhere in the module.
    for name in ("main.tf", "variables.tf", "outputs.tf"):
        assert not re.search(r"[0-9]{12}", read(name)), f"account literal in {name}"


def test_module_is_provider_agnostic_reusable() -> None:
    # A reusable child module declares its provider requirement but configures
    # no provider block (the caller/overlay supplies the configured provider).
    main = read("main.tf")
    assert "required_providers" in main
    assert not re.search(r'provider\s+"aws"\s*{', main), (
        "reusable module must not embed a provider block"
    )


def test_exposes_table_arn_and_name_outputs() -> None:
    outputs = read("outputs.tf")
    assert 'output "table_name"' in outputs
    assert 'output "table_arn"' in outputs
