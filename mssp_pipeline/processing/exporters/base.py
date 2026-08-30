from typing import Protocol, runtime_checkable

from ..sql import join_identifiers, sql_string_literal, validate_identifier


def normalize_identifier(name: str) -> str:
    """Normalize a table or column name to lowercase for cross-warehouse consistency."""
    return validate_identifier(name.lower(), field_name="identifier")


def qualified_identifier(*parts: str, field_name: str = "identifier") -> str:
    return join_identifiers(*parts, field_name=field_name)


def string_literal(value: str) -> str:
    return sql_string_literal(value)


def normalize_query(query: str, conn) -> str:
    """Wrap query so all output column names are lowercase."""
    cols = conn.execute(f"DESCRIBE SELECT * FROM ({query}) AS _q").fetchall()
    aliases = ", ".join(f'"{c[0]}" AS {c[0].lower()}' for c in cols)
    return f"SELECT {aliases} FROM ({query}) AS _q"


@runtime_checkable
class Exporter(Protocol):
    """
    Strategy interface for writing query results to a backend.

    Implementors: ParquetExporter, DuckDBExporter, SnowflakeExporter, etc.
    New backends need to implement both methods.

    The query passed to export() is pre-filtered by FileProcessor.run() to
    contain only rows from source files not yet in the destination — no
    additional FILE_PATH deduplication is needed inside export().
    """

    def export(self, query: str, table_name: str, duckdb_connection) -> None:
        """
        Execute `query` via `duckdb_connection` and persist the result.

        The query already contains only new rows. The exporter decides whether
        to create a new table or append based on whether the destination exists.

        Args:
            query:             A DuckDB SELECT statement (new rows only).
            table_name:        Logical table name (e.g. 'parta_claims_header').
                               The exporter resolves the physical destination internally.
            duckdb_connection: An active duckdb.DuckDBPyConnection.
        """
        ...

    def get_missing_file_paths(self, table_name: str, candidate_file_paths: list[str], duckdb_connection) -> list[str]:
        """
        Return only the candidate FILE_PATH values not yet persisted in the destination.

        Called by FileProcessor.run() batch-by-batch so exporters can perform
        bounded existence checks against the destination backend.

        Args:
            table_name:        Logical table name.
            candidate_file_paths: FILE_PATH values from the current processor batch.
            duckdb_connection: An active duckdb.DuckDBPyConnection.
        """
        ...
