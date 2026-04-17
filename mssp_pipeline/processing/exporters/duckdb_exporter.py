from .base import normalize_identifier, normalize_query, qualified_identifier, string_literal


class DuckDBExporter:
    def __init__(self, schema: str = "raw_data", full_refresh: bool = False):
        self.schema = normalize_identifier(schema)
        self.full_refresh = full_refresh

    def export(self, query: str, table_name: str, duckdb_connection) -> None:
        table_name = normalize_identifier(table_name)
        query = normalize_query(query, duckdb_connection)
        destination = qualified_identifier(self.schema, table_name, field_name="table reference")
        duckdb_connection.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")

        table_exists = duckdb_connection.execute(f"""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = {string_literal(self.schema)}
              AND table_name   = {string_literal(table_name)}
        """).fetchone()[0] > 0

        if self.full_refresh or not table_exists:
            print(f"Writing results to Table: {destination}")
            duckdb_connection.execute(f"CREATE OR REPLACE TABLE {destination} AS {query}")
        else:
            print(f"Appending new rows into {destination}.")
            duckdb_connection.execute(
                f"INSERT INTO {destination} SELECT * FROM ({query}) AS src"
            )

        print("Export successful.")

    def get_existing_file_paths(self, table_name: str, duckdb_connection) -> list:
        """Return distinct FILE_PATH values from the destination table, or [] if it doesn't exist."""
        table_name = normalize_identifier(table_name)
        table_exists = duckdb_connection.execute(f"""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = {string_literal(self.schema)}
              AND table_name   = {string_literal(table_name)}
        """).fetchone()[0] > 0
        if not table_exists:
            return []
        return [
            row[0]
            for row in duckdb_connection.execute(
                f"SELECT DISTINCT FILE_PATH FROM {qualified_identifier(self.schema, table_name, field_name='table reference')}"
            ).fetchall()
        ]
