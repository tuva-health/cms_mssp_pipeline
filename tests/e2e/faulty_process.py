"""`mssp-process` with a deliberately broken Parquet exporter.

Used only by the synthetic end-to-end run (`tests/e2e/synthetic_run.py`) to
prove its checks catch exporter regressions. Each fault patches the real
exporter in-process and then hands over to the unmodified `mssp-process` entry
point, so everything except the injected defect is production code.

    python -m tests.e2e.faulty_process uppercase-columns
    python -m tests.e2e.faulty_process missing-table
"""

from __future__ import annotations

import sys

# A non-optional workbook relation: the contract requires it in every delivery.
DROPPED_TABLE = "bnmrk_table_1"

FAULTS = ("uppercase-columns", "missing-table")


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


def main(argv: list[str]) -> None:
    if len(argv) != 1 or argv[0] not in FAULTS:
        raise SystemExit(f"usage: faulty_process {{{','.join(FAULTS)}}}")
    {"uppercase-columns": _uppercase_columns, "missing-table": _missing_table}[argv[0]]()

    from mssp_pipeline.__main__ import process_main

    sys.argv = ["mssp-process"]
    process_main()


if __name__ == "__main__":
    main(sys.argv[1:])
