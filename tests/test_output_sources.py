"""InformationSchemaOutputSource: a generic sequencer ``output_source`` that
reads a warehouse's ``information_schema.tables`` and reports where each
contracted output actually landed.

Driven against a fake DB-API connection -- no warehouse, no credentials. The
database / schema / table names are synthetic.
"""

from __future__ import annotations

import pytest

from mssp_pipeline.output_contract import AcceptedOutputContract, verify_outputs
from mssp_pipeline.output_sources import InformationSchemaOutputSource
from mssp_pipeline.sequencer import Stage

PLACEMENT = {"database": "DB_DEV", "schema": "RAW"}


class FakeCursor:
    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self._rows = rows
        self.executed: list[str] = []

    def execute(self, sql: str) -> None:
        self.executed.append(sql)

    def fetchall(self) -> list[tuple[str, str]]:
        return list(self._rows)

    def close(self) -> None:
        pass


class FakeConnection:
    """A DB-API-shaped connection whose ``information_schema.tables`` query
    answers with the configured ``(table_schema, table_name)`` rows --
    UPPERCASE, as a warehouse reports unquoted identifiers."""

    def __init__(self, rows: list[tuple[str, str]]) -> None:
        self._cursor = FakeCursor(rows)
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def _stage(contract: AcceptedOutputContract) -> Stage:
    return Stage(
        name="raw",
        taskdef_family="raw",
        expected_task_revision="arn:aws:ecs:us-east-1:123456789012:task-definition/raw:1",
        output_contract=contract,
    )


def test_good_run_reports_every_contracted_table_at_its_placement() -> None:
    contract = AcceptedOutputContract({"TABLE_A": PLACEMENT, "TABLE_B": PLACEMENT})
    conn = FakeConnection(
        [("RAW", "TABLE_A"), ("RAW", "TABLE_B")]
    )
    source = InformationSchemaOutputSource(connect=lambda: conn)

    produced = source(_stage(contract))

    assert produced == {"TABLE_A": PLACEMENT, "TABLE_B": PLACEMENT}
    assert verify_outputs(contract, produced) == []
    assert conn.closed


def test_missing_table_is_absent_so_the_contract_fails() -> None:
    contract = AcceptedOutputContract({"TABLE_A": PLACEMENT, "TABLE_B": PLACEMENT})
    conn = FakeConnection([("RAW", "TABLE_A")])
    source = InformationSchemaOutputSource(connect=lambda: conn)

    produced = source(_stage(contract))

    assert set(produced) == {"TABLE_A"}
    violations = verify_outputs(contract, produced)
    assert violations == ["output 'TABLE_B' is missing from the produced set"]


def test_table_in_the_wrong_schema_reports_the_observed_schema() -> None:
    contract = AcceptedOutputContract({"TABLE_A": PLACEMENT})
    conn = FakeConnection([("ELSEWHERE", "TABLE_A")])
    source = InformationSchemaOutputSource(connect=lambda: conn)

    produced = source(_stage(contract))

    assert produced == {"TABLE_A": {"database": "DB_DEV", "schema": "ELSEWHERE"}}
    violations = verify_outputs(contract, produced)
    assert violations == [
        "output 'TABLE_A' coordinate 'schema' is 'ELSEWHERE', expected 'RAW'"
    ]


def test_uppercase_catalogue_names_match_a_contract_named_in_any_case() -> None:
    """information_schema reports UPPERCASE; the contract may not. The produced
    map is keyed by the contract's spelling so verify_outputs compares equal."""
    lower = {"database": "db_dev", "schema": "raw"}
    contract = AcceptedOutputContract({"table_a": lower, "Table_B": lower})
    conn = FakeConnection(
        [("RAW", "TABLE_A"), ("RAW", "TABLE_B")]
    )
    source = InformationSchemaOutputSource(connect=lambda: conn)

    produced = source(_stage(contract))

    assert produced == {"table_a": lower, "Table_B": lower}
    assert verify_outputs(contract, produced) == []


def test_tables_the_contract_does_not_name_are_not_reported() -> None:
    """The schema hosts other table families the contract does not govern;
    they are neither reported nor treated as extras."""
    contract = AcceptedOutputContract({"TABLE_A": PLACEMENT})
    conn = FakeConnection(
        [("RAW", "TABLE_A"), ("RAW", "OTHER_FAMILY_1")]
    )
    source = InformationSchemaOutputSource(connect=lambda: conn)

    produced = source(_stage(contract))

    assert produced == {"TABLE_A": PLACEMENT}
    assert verify_outputs(contract, produced) == []


def test_reads_the_contracted_database_catalogue_base_tables_only() -> None:
    contract = AcceptedOutputContract({"TABLE_A": PLACEMENT})
    conn = FakeConnection([("RAW", "TABLE_A")])
    source = InformationSchemaOutputSource(connect=lambda: conn)

    source(_stage(contract))

    (sql,) = conn.cursor().executed
    assert "FROM DB_DEV.information_schema.tables" in sql
    assert "table_type = 'BASE TABLE'" in sql


def test_rejects_a_database_coordinate_that_is_not_a_bare_identifier() -> None:
    contract = AcceptedOutputContract(
        {"TABLE_A": {"database": "DB_DEV; DROP TABLE x", "schema": "RAW"}}
    )
    conn = FakeConnection([])
    source = InformationSchemaOutputSource(connect=lambda: conn)

    with pytest.raises(ValueError, match="database"):
        source(_stage(contract))
    assert conn.cursor().executed == []


def test_stage_without_a_contract_produces_nothing_and_never_connects() -> None:
    calls: list[int] = []

    def connect():
        calls.append(1)
        return FakeConnection([])

    source = InformationSchemaOutputSource(connect=connect)
    stage = Stage(
        name="dbt",
        taskdef_family="dbt",
        expected_task_revision="arn:aws:ecs:us-east-1:123456789012:task-definition/dbt:1",
    )

    assert source(stage) == {}
    assert calls == []
