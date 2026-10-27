"""`mssp-process` with a deliberately broken Parquet exporter.

Used only by the synthetic end-to-end run (`tests/e2e/synthetic_run.py`) to
prove its checks catch exporter regressions. Each fault patches the real
exporter in-process and then hands over to the unmodified `mssp-process` entry
point, so everything except the injected defect is production code.

    python -m tests.e2e.faulty_process uppercase-columns
    python -m tests.e2e.faulty_process missing-table
    python -m tests.e2e.faulty_process cclf-source-drift
"""

from __future__ import annotations

import sys

# A non-optional workbook relation: the contract requires it in every delivery.
DROPPED_TABLE = "bnmrk_table_1"

# A column medicare_cclf_connector's partb_demo_codes source declares (TUVA-125).
DRIFTED_CCLF_TABLE = "partb_demo_codes"
DRIFTED_CCLF_COLUMN = "clm_line_carr_hpsa_scrcty_cd"

def _uppercase_columns() -> None:
    """Exporter forgets the lowercase normalisation the contract requires and
    writes every column name upper-cased instead."""
    from mssp_pipeline.processing.exporters import parquet_exporter

    def normalize_query(query, conn):
        cols = conn.execute(f"DESCRIBE SELECT * FROM ({query}) AS _q").fetchall()
        aliases = ", ".join(f'"{c[0]}" AS "{c[0].upper()}"' for c in cols)
        return f"SELECT {aliases} FROM ({query}) AS _q"

    parquet_exporter.normalize_query = normalize_query


def _missing_table() -> None:
    """Exporter silently skips one contracted table."""
    from mssp_pipeline.processing.exporters.parquet_exporter import ParquetExporter

    original = ParquetExporter.export

    def export(self, query, table_name, duckdb_connection):
        if table_name.lower() == DROPPED_TABLE:
            print(f"[fault] not exporting {table_name}")
            return None
        return original(self, query, table_name, duckdb_connection)

    ParquetExporter.export = export


def _cclf_source_drift() -> None:
    """Exporter writes a raw CCLF table without a column the CCLF connector reads."""
    from mssp_pipeline.processing.exporters.parquet_exporter import ParquetExporter

    original = ParquetExporter.export

    def export(self, query, table_name, duckdb_connection):
        if table_name.lower() == DRIFTED_CCLF_TABLE:
            print(f"[fault] exporting {table_name} without {DRIFTED_CCLF_COLUMN}")
            query = f"SELECT * EXCLUDE ({DRIFTED_CCLF_COLUMN}) FROM ({query}) AS _q"
        return original(self, query, table_name, duckdb_connection)

    ParquetExporter.export = export


# Exporter faults, by --fault name. synthetic_run routes these through this module.
FAULTS = {
    "uppercase-columns": _uppercase_columns,
    "missing-table": _missing_table,
    "cclf-source-drift": _cclf_source_drift,
}


def main(argv: list[str]) -> None:
    if len(argv) != 1 or argv[0] not in FAULTS:
        raise SystemExit(f"usage: faulty_process {{{','.join(FAULTS)}}}")
    FAULTS[argv[0]]()

    from mssp_pipeline.__main__ import process_main

    sys.argv = ["mssp-process"]
    process_main()


if __name__ == "__main__":
    main(sys.argv[1:])
