from typing import Protocol, runtime_checkable


def normalize_identifier(name: str) -> str:
    """Normalize a table or column name to lowercase for cross-warehouse consistency."""
    return name.lower()


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

    def get_existing_file_paths(self, table_name: str, duckdb_connection) -> list:
        """
        Return the list of FILE_PATH values already persisted in the destination.

        Returns an empty list if the table/file does not yet exist.
        Called by FileProcessor.run() to determine which source files are new.

        Args:
            table_name:        Logical table name.
            duckdb_connection: An active duckdb.DuckDBPyConnection.
        """
        ...
