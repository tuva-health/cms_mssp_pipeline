"""Export-conformance tests for the workbook v1 contract.

`contracts/workbook/v1.json` publishes the canonical, machine-readable
20-relation cell-grain workbook API: version, the twenty BNMRK / AEXPU / QEXPU
relations, their shared cell-grain schema (grammar plus path-derived metadata
columns), optional-sheet behavior, and the accepted output set.

These tests assert two independent things:

1. The *contract itself* matches the code it describes — the set of relations,
   their sheet patterns, and their families are derived from the concrete
   ``*_SHEET_DEFS`` in the repo, never invented, so a def that drifts from the
   published contract fails here.

2. The *exports conform to the contract* — the three concrete processors are run
   against synthetic (non-PHI) fixtures and every materialised table is checked,
   column-for-column and type-for-type, against the schema the contract declares
   for it, including the zero-row shape an absent optional sheet produces.

The synthetic fixtures are reused verbatim from
``test_aco_workbook_processors.py`` so no agreement data appears here.
"""

import importlib
import json
import re
from pathlib import Path

import pytest

from mssp_pipeline.processing.defs.aexpu_file_defs import AEXPU_SHEET_DEFS
from mssp_pipeline.processing.defs.bnmrk_file_defs import BNMRK_SHEET_DEFS
from mssp_pipeline.processing.defs.qexpu_file_defs import QEXPU_SHEET_DEFS

from mssp_pipeline.processing.processors.aexpu_processor import AEXPUProcessor
from mssp_pipeline.processing.processors.bnmrk_processor import BNMRKProcessor
from mssp_pipeline.processing.processors.qexpu_processor import QEXPUProcessor

from .test_aco_workbook_processors import (
    config_for,
    make_bnmrk_bundle,
    make_qexpu_bundle,
    run,
)

# ---------------------------------------------------------------------------
# Contract location + independent source of truth
# ---------------------------------------------------------------------------

CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "workbook" / "v1.json"
)

# The relations, families and sheet patterns the contract must describe, derived
# straight from the concrete defs — this is the independent truth the published
# contract is checked against.
DEFS_BY_FAMILY = {
    "BNMRK": BNMRK_SHEET_DEFS,
    "AEXPU": AEXPU_SHEET_DEFS,
    "QEXPU": QEXPU_SHEET_DEFS,
}

EXPECTED_RELATIONS = {
    d.table_name: (family, d) for family, defs in DEFS_BY_FAMILY.items() for d in defs
}

# Sheets that are legitimately absent from some conforming deliveries, documented
# in the defs/processors: BNMRK Table 6 (June/October only) and the AEXPU COVID
# variants (benchmark years BY1/BY2 only). Everything else is expected in every
# delivery. This literal is the independent truth for the ``optional`` flag.
EXPECTED_OPTIONAL = {"BNMRK_TABLE_6", "AEXPU_TABLE_1A", "AEXPU_TABLE_4A"}


@pytest.fixture(scope="module")
def contract():
    with open(CONTRACT_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Contract structure
# ---------------------------------------------------------------------------


def test_contract_declares_an_exact_version(contract):
    """The contract is exactly versioned — v1 is pinned, not open-ended."""
    assert contract["version"] == "1.0.0"
    assert contract["grain"] == "cell"


def test_contract_declares_exactly_the_twenty_relations(contract):
    """Twenty relations, matching the BNMRK/AEXPU/QEXPU defs one-for-one.

    Derived from the concrete ``*_SHEET_DEFS`` so a relation added to or removed
    from the code without updating the published contract fails here.
    """
    declared = {r["relation"] for r in contract["relations"]}
    assert len(contract["relations"]) == 20
    assert declared == set(EXPECTED_RELATIONS)


def test_each_relation_matches_its_def(contract):
    """Family, sheet pattern and exported table name match the def exactly."""
    for r in contract["relations"]:
        family, sheet_def = EXPECTED_RELATIONS[r["relation"]]
        assert r["family"] == family, r["relation"]
        assert r["sheet_pattern"] == sheet_def.sheet_pattern, r["relation"]
        # Exporters normalise identifiers to lowercase for cross-warehouse
        # consistency, so the exported table is the lowercased relation name.
        assert r["table"] == r["relation"].lower(), r["relation"]


def test_optional_relations_are_exactly_the_documented_optional_sheets(contract):
    """The ``optional`` flag marks the sheets that may be absent — and only those."""
    declared_optional = {
        r["relation"] for r in contract["relations"] if r.get("optional", False)
    }
    assert declared_optional == EXPECTED_OPTIONAL


def test_accepted_output_set_is_all_twenty_relations(contract):
    """The audit-preserving benchmark graph is the full set of twenty relations."""
    assert set(contract["accepted_output_set"]) == set(EXPECTED_RELATIONS)
    assert len(contract["accepted_output_set"]) == 20


# ---------------------------------------------------------------------------
# Legacy EXPU stays absent
# ---------------------------------------------------------------------------


def test_no_legacy_expu_relation_is_declared(contract):
    """Legacy EXPU is retired: it appears nowhere in the published contract.

    A standalone-EXPU relation is one whose family/name/table begins with EXPU
    on its own — the leading '^' is what excludes the annual/quarterly AEXPU and
    QEXPU relations, whose names merely *contain* 'EXPU'. Adding an
    ``EXPU_TABLE_1`` / ``EXPU_PARAMETERS`` relation (family "EXPU") would fail
    here.
    """
    legacy = re.compile(r"^EXPU(_|$)")
    for r in contract["relations"]:
        assert not legacy.match(r["family"]), r["family"]
        assert not legacy.match(r["relation"]), r["relation"]
        assert not legacy.match(r["table"].upper()), r["table"]


@pytest.mark.parametrize(
    "module_name",
    [
        "mssp_pipeline.processing.defs.expu_file_defs",
        "mssp_pipeline.processing.processors.expu_processor",
    ],
)
def test_legacy_expu_modules_are_absent(module_name):
    """The retired EXPU code cannot be imported — it is gone from the tree."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module_name)


# ---------------------------------------------------------------------------
# Export conformance — the exports match the declared schema
# ---------------------------------------------------------------------------


def _declared_columns(contract, relation):
    """Ordered [(name, type), ...] the contract declares for a relation."""
    schema_name = next(
        r["schema"] for r in contract["relations"] if r["relation"] == relation
    )
    return [
        (c["name"], c["type"])
        for c in contract["schemas"][schema_name]["columns"]
    ]


def _exported_columns(conn, table):
    """Ordered [(name, type), ...] actually materialised in raw_data.<table>."""
    return [
        (name, data_type)
        for name, data_type in conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'raw_data' AND table_name = ? "
            "ORDER BY ordinal_position",
            [table],
        ).fetchall()
    ]


def _table_exists(conn, table):
    return conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = 'raw_data' AND table_name = ?",
        [table],
    ).fetchone()[0] > 0


@pytest.fixture
def all_exports(test_session, raw_dir):
    """Run all three processors over a full synthetic store; return the connection.

    The BNMRK bundle carries the BNMRK workbook plus one AEXPU workbook per
    benchmark year (BY1/BY2 include the COVID variants, BY3 omits them), and the
    QEXPU bundle carries the quarterly workbook — between them every one of the
    twenty relations has a source sheet to materialise from.
    """
    make_bnmrk_bundle(raw_dir)
    make_qexpu_bundle(raw_dir)
    config = config_for(raw_dir)
    for processor_cls in (BNMRKProcessor, AEXPUProcessor, QEXPUProcessor):
        run(processor_cls, test_session, config)
    return test_session.connection


def test_every_relation_in_the_accepted_output_set_materialises(all_exports, contract):
    """All twenty declared relations are produced from a full delivery."""
    produced = {
        row[0]
        for row in all_exports.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'raw_data'"
        ).fetchall()
    }
    expected = {r["table"] for r in contract["relations"]}
    assert expected <= produced


@pytest.mark.parametrize("relation", sorted(EXPECTED_RELATIONS))
def test_exported_relation_conforms_to_its_declared_schema(
    all_exports, contract, relation
):
    """Each export matches its contract schema column-for-column and type-for-type.

    The declared schema comes from the JSON contract; the observed schema comes
    from DuckDB's information_schema after a real processor run — two independent
    sources, so a drift in either direction fails here.
    """
    table = relation.lower()
    assert _table_exists(all_exports, table), relation
    assert _exported_columns(all_exports, table) == _declared_columns(
        contract, relation
    ), relation


def test_absent_optional_sheet_yields_a_conformant_zero_row_table(
    test_session, raw_dir, contract
):
    """An optional sheet's absence is not an error: the relation still exists,
    with zero rows and its full declared schema.

    Only a March-preliminary BNMRK delivery is written (no 'Table 6 - ACPT'), so
    bnmrk_table_6 has no source sheet in the whole store.
    """
    make_bnmrk_bundle(raw_dir, with_table_6=False)
    conn = run(BNMRKProcessor, test_session, config_for(raw_dir))

    assert _table_exists(conn, "bnmrk_table_6")
    assert conn.execute("SELECT COUNT(*) FROM raw_data.bnmrk_table_6").fetchone()[0] == 0
    assert _exported_columns(conn, "bnmrk_table_6") == _declared_columns(
        contract, "BNMRK_TABLE_6"
    )
