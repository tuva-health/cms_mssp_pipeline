import os

from .base import normalize_identifier, normalize_query

_CLOUD_PREFIXES = ('s3://', 'az://', 'azure://', 'abfss://', 'gs://')


class ParquetExporter:
    def __init__(self, output_dir: str, full_refresh: bool = False):
        self.output_dir = output_dir
        self.full_refresh = full_refresh
        self._is_cloud = output_dir.startswith(_CLOUD_PREFIXES)

    def _destination(self, table_name: str) -> str:
        return f"{self.output_dir.rstrip('/')}/{table_name}.parquet"

    def _file_exists(self, duckdb_connection, path: str) -> bool:
        """Check whether a Parquet file exists — works for local and cloud paths."""
        try:
            duckdb_connection.execute(f"SELECT 1 FROM read_parquet('{path}') LIMIT 0")
            return True
        except Exception:
            return False

    def export(self, query: str, table_name: str, duckdb_connection) -> None:
        table_name = normalize_identifier(table_name)
        query = normalize_query(query, duckdb_connection)
        destination = self._destination(table_name)
        existing = self._file_exists(duckdb_connection, destination)

        if self.full_refresh or not existing:
            if existing and not self._is_cloud:
                os.remove(destination)
            print(f"Exporting results to Parquet: {destination}")
            duckdb_connection.execute(f"COPY ({query}) TO '{destination}' (FORMAT PARQUET)")
        else:
            print(f"Appending new rows to Parquet: {destination}")
            if self._is_cloud:
                # Cloud storage has no atomic rename; write merged result directly.
                duckdb_connection.execute(f"""
                    COPY (
                        SELECT * FROM read_parquet('{destination}')
                        UNION ALL
                        SELECT * FROM ({query}) AS src
                    ) TO '{destination}' (FORMAT PARQUET)
                """)
            else:
                tmp_path = destination + '.tmp'
                duckdb_connection.execute(f"""
                    COPY (
                        SELECT * FROM read_parquet('{destination}')
                        UNION ALL
                        SELECT * FROM ({query}) AS src
                    ) TO '{tmp_path}' (FORMAT PARQUET)
                """)
                os.replace(tmp_path, destination)

        print("Export successful.")

    def get_existing_file_paths(self, table_name: str, duckdb_connection) -> list:
        """Return distinct FILE_PATH values from the existing Parquet file, or [] if not found.

        Uses read_parquet() rather than os.path.exists() so that cloud-backed output
        locations (s3://, az://, abfss://) are handled correctly — os.path.exists()
        always returns False for cloud URIs.
        """
        table_name = normalize_identifier(table_name)
        destination = self._destination(table_name)
        try:
            return [
                row[0]
                for row in duckdb_connection.execute(
                    f"SELECT DISTINCT FILE_PATH FROM read_parquet('{destination}')"
                ).fetchall()
            ]
        except Exception:
            return []
