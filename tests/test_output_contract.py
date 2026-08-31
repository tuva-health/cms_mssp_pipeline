"""Output-set / placement verification from an accepted-output contract.

A run must produce exactly the accepted set of outputs, each at its contracted
placement (its coordinate map), with no extras, omissions, drifted coordinates,
or forbidden fields. Output names and coordinate values here are synthetic.
"""

from __future__ import annotations

from mssp_pipeline.output_contract import AcceptedOutputContract, verify_outputs

CONTRACT = AcceptedOutputContract(
    {
        "raw_dev": {"database": "DB_DEV", "schema": "RAW"},
        "raw_prod": {"database": "DB_PROD", "schema": "RAW"},
    },
    forbidden_fields=("password",),
)


def test_exact_match_passes() -> None:
    produced = {
        "raw_dev": {"database": "DB_DEV", "schema": "RAW"},
        "raw_prod": {"database": "DB_PROD", "schema": "RAW"},
    }
    assert verify_outputs(CONTRACT, produced) == []


def test_missing_output_is_a_violation() -> None:
    produced = {"raw_dev": {"database": "DB_DEV", "schema": "RAW"}}
    violations = verify_outputs(CONTRACT, produced)
    assert any("raw_prod" in v and "missing" in v for v in violations)


def test_unexpected_output_is_a_violation() -> None:
    produced = {
        "raw_dev": {"database": "DB_DEV", "schema": "RAW"},
        "raw_prod": {"database": "DB_PROD", "schema": "RAW"},
        "raw_extra": {"database": "X", "schema": "RAW"},
    }
    violations = verify_outputs(CONTRACT, produced)
    assert any("raw_extra" in v and "unexpected" in v for v in violations)


def test_drifted_placement_coordinate_is_a_violation() -> None:
    produced = {
        "raw_dev": {"database": "WRONG", "schema": "RAW"},
        "raw_prod": {"database": "DB_PROD", "schema": "RAW"},
    }
    violations = verify_outputs(CONTRACT, produced)
    assert any("raw_dev" in v and "database" in v for v in violations)


def test_extra_coordinate_key_is_a_violation() -> None:
    produced = {
        "raw_dev": {"database": "DB_DEV", "schema": "RAW", "role": "ADMIN"},
        "raw_prod": {"database": "DB_PROD", "schema": "RAW"},
    }
    violations = verify_outputs(CONTRACT, produced)
    assert any("raw_dev" in v and "role" in v for v in violations)


def test_forbidden_field_is_a_violation() -> None:
    produced = {
        "raw_dev": {"database": "DB_DEV", "schema": "RAW", "password": "x"},
        "raw_prod": {"database": "DB_PROD", "schema": "RAW"},
    }
    violations = verify_outputs(CONTRACT, produced)
    assert any("password" in v for v in violations)


def test_accepted_outputs_property() -> None:
    assert CONTRACT.accepted == frozenset({"raw_dev", "raw_prod"})
