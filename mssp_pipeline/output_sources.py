"""Warehouse-backed output sources for the sequencer.

The engine (`sequencer.py`) verifies a stage's accepted-output contract
(`output_contract.py`) against a *produced placement map* handed back by an
injected ``output_source(stage)`` callable. This module ships a generic,
client-neutral source that derives that map from the warehouse's ANSI
``information_schema.tables`` view -- the same catalogue the Snowflake exporter
already consults -- so the sequencer-side contract verifies what a stage
actually created rather than trusting the task's exit code alone.

:class:`InformationSchemaOutputSource` is **contract-directed**: it answers
"for each output the stage's contract names, where did it land?"

* an output found in its contracted database + schema is reported at the
  contract's own placement (the verify passes for it);
* an output found in that database but in a different schema is reported at
  the placement actually observed (the verify flags the coordinate drift);
* an output not found at all is omitted (the verify flags it missing).

Tables the contract does not name are never reported. A schema legitimately
hosts other table families produced by the same stage (the contract governs
one accepted set, not the whole schema), so the contract's "no extras" rule is
applied over the *named* outputs, not over everything in the schema.

Identifier case follows unquoted-identifier semantics: ``information_schema``
reports UPPERCASE names, while a contract may name outputs in any case, so
names, databases and schemas are compared case-insensitively and the produced
map is keyed by the contract's own spelling (which is what ``verify_outputs``
compares against).

The connection is injected as a zero-argument ``connect`` factory returning a
DB-API-shaped connection (``cursor().execute/fetchall``, ``close``); which
warehouse, account, role and credential it uses is A-class policy supplied by
the overlay. Note that ``information_schema`` lists only the objects the
session's role is privileged to see, so an under-granted role reads as
"missing" -- the overlay must connect with a role that can see the outputs.
Tests use an in-memory fake.
"""

from __future__ import annotations

from contextlib import closing
from typing import Any, Callable, Mapping, Protocol

from mssp_pipeline.processing.sql import validate_identifier
from mssp_pipeline.sequencer import Stage


# The contract placement coordinates that carry the warehouse database and
# schema (the sequencer's own tests use the same two).
DATABASE_COORDINATE = "database"
SCHEMA_COORDINATE = "schema"


class _Cursor(Protocol):
    def execute(self, sql: str) -> Any: ...

    def fetchall(self) -> list: ...

    def close(self) -> None: ...


class Connection(Protocol):
    """The DB-API slice the source needs."""

    def cursor(self) -> _Cursor: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[], Connection]


class InformationSchemaOutputSource:
    """A sequencer ``output_source`` reading ``information_schema.tables``.

    Contract placements must carry :data:`DATABASE_COORDINATE` and
    :data:`SCHEMA_COORDINATE`; any other coordinates are echoed unchanged for
    outputs found where contracted.
    """

    def __init__(self, *, connect: ConnectionFactory) -> None:
        self._connect = connect

    def __call__(self, stage: Stage) -> Mapping[str, Mapping[str, str]]:
        contract = stage.output_contract
        if contract is None:
            return {}

        # Group the contracted outputs by database: one catalogue read per db.
        expected = {name: contract.placement(name) for name in sorted(contract.accepted)}
        by_database: dict[str, list[str]] = {}
        for name, placement in expected.items():
            by_database.setdefault(placement[DATABASE_COORDINATE], []).append(name)

        produced: dict[str, dict[str, str]] = {}
        for database, names in by_database.items():
            observed = self._base_tables(database)
            for name in names:
                placement = expected[name]
                schemas = observed.get(name.upper(), ())
                if placement[SCHEMA_COORDINATE].upper() in {s.upper() for s in schemas}:
                    produced[name] = dict(placement)
                elif schemas:
                    # Present in the database, but not where contracted: report
                    # the observed placement so the verify names the drift.
                    produced[name] = {**placement, SCHEMA_COORDINATE: sorted(schemas)[0]}
        return produced

    def _base_tables(self, database: str) -> dict[str, set[str]]:
        """``{TABLE_NAME (upper): {schema, ...}}`` for the base tables in
        ``database``, as the warehouse's information_schema reports them."""
        db_ident = validate_identifier(database, field_name="database")
        sql = (
            "SELECT table_schema, table_name "
            f"FROM {db_ident}.information_schema.tables "
            "WHERE table_type = 'BASE TABLE'"
        )
        tables: dict[str, set[str]] = {}
        with closing(self._connect()) as conn, closing(conn.cursor()) as cursor:
            cursor.execute(sql)
            for schema, name in cursor.fetchall():
                tables.setdefault(str(name).upper(), set()).add(str(schema))
        return tables
