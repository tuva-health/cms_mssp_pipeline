import os
from snowflake import connector
from cryptography.hazmat.primitives import serialization

from .base import normalize_identifier, normalize_query, qualified_identifier, string_literal


class SnowflakeExporter:
    """
    Exports query results to Snowflake via a Parquet staging file.

    Merges the previously separate export_to_snowflake() and
    transfer_duckdb_to_snowflake() functions from utilities.py.
    """

    def __init__(self, sf_config, staging_dir: str, full_refresh: bool = False):
        """
        Args:
            sf_config:    A SnowflakeConfig dataclass instance (from config.py).
            staging_dir:  Local directory for temporary Parquet files.
            full_refresh: If True, drop and recreate the Snowflake table on every run.
                          If False (default), only append rows from new source files.
        """
        self.sf_config = sf_config
        self.staging_dir = staging_dir
        self.full_refresh = full_refresh

    def export(self, query: str, table_name: str, duckdb_connection) -> None:
        table_name = normalize_identifier(table_name)
        query = normalize_query(query, duckdb_connection)
        parquet_path = os.path.join(self.staging_dir, f'{table_name}.parquet')
        if os.path.exists(parquet_path):
            os.remove(parquet_path)

        table_exists_in_sf = self._snowflake_table_exists(table_name)
        is_incremental = (not self.full_refresh) and table_exists_in_sf

        print(f"Writing staging Parquet: {parquet_path}")
        duckdb_connection.execute(f"COPY ({query}) TO {string_literal(parquet_path)} (FORMAT PARQUET)")

        if is_incremental:
            self._append_to_snowflake(parquet_path, table_name)
        else:
            self._upload_to_snowflake(parquet_path, table_name)

    def get_existing_file_paths(self, table_name: str, duckdb_connection) -> list:
        """Return distinct FILE_PATH values from the existing Snowflake table, or [] if not found."""
        table_name = normalize_identifier(table_name)
        full_table_name = self._table_ref(table_name)
        private_key = self._load_rsa_key()
        snowflake_conn = connector.connect(
            user=self.sf_config.username,
            account=self.sf_config.account,
            schema=self.sf_config.schema,
            database=self.sf_config.database,
            warehouse=self.sf_config.warehouse,
            private_key=private_key,
            role=self.sf_config.role,
        )
        cursor = snowflake_conn.cursor()
        try:
            file_path_column = self._file_path_column(cursor, table_name)
            cursor.execute(f"SELECT DISTINCT {file_path_column} FROM {full_table_name}")
            paths = [row[0] for row in cursor.fetchall()]
            print(f"  Found {len(paths)} existing FILE_PATH(s) in Snowflake for {table_name}")
            return paths
        except connector.errors.ProgrammingError as e:
            if "does not exist" in str(e).lower():
                return []
            raise
        finally:
            cursor.close()
            snowflake_conn.close()

    def get_missing_file_paths(self, table_name: str, candidate_file_paths: list[str], duckdb_connection) -> list[str]:
        table_name = normalize_identifier(table_name)
        if not candidate_file_paths:
            return []
        if not self._snowflake_table_exists(table_name):
            return list(candidate_file_paths)

        full_table_name = self._table_ref(table_name)
        private_key = self._load_rsa_key()
        snowflake_conn = connector.connect(
            user=self.sf_config.username,
            account=self.sf_config.account,
            schema=self.sf_config.schema,
            database=self.sf_config.database,
            warehouse=self.sf_config.warehouse,
            private_key=private_key,
            role=self.sf_config.role,
        )
        cursor = snowflake_conn.cursor()
        try:
            file_path_column = self._file_path_column(cursor, table_name)
            cursor.execute(f"""
                WITH candidate_paths AS (
                    {self._candidate_paths_sql(candidate_file_paths, file_path_column)}
                )
                SELECT c.{file_path_column}
                FROM candidate_paths c
                LEFT JOIN {full_table_name} t
                  ON t.{file_path_column} = c.{file_path_column}
                WHERE t.{file_path_column} IS NULL
            """)
            missing = {row[0] for row in cursor.fetchall()}
            return [path for path in candidate_file_paths if path in missing]
        except connector.errors.ProgrammingError as e:
            if "does not exist" in str(e).lower():
                return list(candidate_file_paths)
            raise
        finally:
            cursor.close()
            snowflake_conn.close()

    def _snowflake_table_exists(self, table_name: str) -> bool:
        """Returns True if the target table already exists in Snowflake."""
        private_key = self._load_rsa_key()
        snowflake_conn = connector.connect(
            user=self.sf_config.username,
            account=self.sf_config.account,
            schema=self.sf_config.schema,
            database=self.sf_config.database,
            warehouse=self.sf_config.warehouse,
            private_key=private_key,
            role=self.sf_config.role,
        )
        cursor = snowflake_conn.cursor()
        try:
            cursor.execute(f"""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = {string_literal(normalize_identifier(self.sf_config.schema).upper())}
                  AND table_name   = {string_literal(table_name.upper())}
            """)
            return cursor.fetchone()[0] > 0
        finally:
            cursor.close()
            snowflake_conn.close()

    def _fetch_existing_file_paths(self, table_name: str) -> list:
        """Fetches all distinct FILE_PATH values from the existing Snowflake table."""
        full_table_name = self._table_ref(table_name)
        private_key = self._load_rsa_key()
        snowflake_conn = connector.connect(
            user=self.sf_config.username,
            account=self.sf_config.account,
            schema=self.sf_config.schema,
            database=self.sf_config.database,
            warehouse=self.sf_config.warehouse,
            private_key=private_key,
            role=self.sf_config.role,
        )
        cursor = snowflake_conn.cursor()
        try:
            file_path_column = self._file_path_column(cursor, table_name)
            cursor.execute(f"SELECT DISTINCT {file_path_column} FROM {full_table_name}")
            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()
            snowflake_conn.close()

    def _append_to_snowflake(self, file_location: str, table_name: str) -> None:
        """Uploads a Parquet file to stage and INSERTs into the existing Snowflake table."""
        private_key = self._load_rsa_key()
        snowflake_conn = connector.connect(
            user=self.sf_config.username,
            account=self.sf_config.account,
            schema=self.sf_config.schema,
            database=self.sf_config.database,
            warehouse=self.sf_config.warehouse,
            private_key=private_key,
            role=self.sf_config.role,
        )
        cursor = snowflake_conn.cursor()
        try:
            stage_name = self._stage_name(table_name)
            full_table_name = self._table_ref(table_name)

            cursor.execute(f"CREATE STAGE IF NOT EXISTS {stage_name}")
            print("Uploading to Snowflake...")
            cursor.execute(
                f"PUT {string_literal(f'file://{file_location}')} @{stage_name} AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
            )

            temp_format_name = self._temp_format_name(table_name)
            cursor.execute(f"""
                CREATE OR REPLACE TEMPORARY FILE FORMAT {temp_format_name}
                TYPE = 'PARQUET'
            """)

            print(f"Appending into {full_table_name}...")
            cursor.execute(f"""
                COPY INTO {full_table_name}
                FROM @{stage_name}
                FILE_FORMAT = (TYPE = 'PARQUET')
                MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
                ON_ERROR = 'ABORT_STATEMENT'
                PURGE = TRUE
            """)

            snowflake_conn.commit()
            print(f"✅ Successfully appended to {full_table_name}")

        except Exception as e:
            print(f"Error appending {table_name} to Snowflake: {e}")
            snowflake_conn.rollback()
            raise
        finally:
            cursor.close()
            snowflake_conn.close()

    def _upload_to_snowflake(self, file_location: str, table_name: str) -> None:
        private_key = self._load_rsa_key()
        snowflake_conn = connector.connect(
            user=self.sf_config.username,
            account=self.sf_config.account,
            schema=self.sf_config.schema,
            database=self.sf_config.database,
            warehouse=self.sf_config.warehouse,
            private_key=private_key,
            role=self.sf_config.role,
        )
        cursor = snowflake_conn.cursor()
        try:
            stage_name = self._stage_name(table_name)
            cursor.execute(f"DROP STAGE IF EXISTS {stage_name}")
            cursor.execute(f"CREATE STAGE IF NOT EXISTS {stage_name}")

            print("Uploading to Snowflake...")
            cursor.execute(
                f"PUT {string_literal(f'file://{file_location}')} @{stage_name} AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
            )

            print("Creating table...")
            temp_format_name = self._temp_format_name(table_name)
            cursor.execute(f"""
                CREATE OR REPLACE TEMPORARY FILE FORMAT {temp_format_name}
                TYPE = 'PARQUET'
            """)

            full_table_name = self._table_ref(table_name)
            cursor.execute(f"""
                CREATE OR REPLACE TABLE {full_table_name}
                USING TEMPLATE (
                    SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*))
                    FROM TABLE(
                        INFER_SCHEMA(
                            LOCATION=> '@{stage_name}',
                            FILE_FORMAT=>{string_literal(temp_format_name)}
                        )
                    )
                );
            """)

            print("Loading into table...")
            cursor.execute(f"""
                COPY INTO {full_table_name}
                FROM @{stage_name}
                FILE_FORMAT = (TYPE = 'PARQUET')
                MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
                ON_ERROR = 'ABORT_STATEMENT'
                PURGE = TRUE
            """)

            snowflake_conn.commit()
            print(f"✅ Successfully loaded {full_table_name}")

        except Exception as e:
            print(f"Error loading {table_name} to Snowflake: {e}")
            snowflake_conn.rollback()
            raise
        finally:
            cursor.close()
            snowflake_conn.close()

    def _table_ref(self, table_name: str) -> str:
        return qualified_identifier(
            normalize_identifier(self.sf_config.schema),
            table_name,
            field_name="table reference",
        )

    def _candidate_paths_sql(self, candidate_file_paths: list[str], file_path_column: str) -> str:
        selects = []
        for idx, path in enumerate(candidate_file_paths):
            literal = string_literal(path)
            if idx == 0:
                selects.append(f"SELECT {literal} AS {file_path_column}")
            else:
                selects.append(f"UNION ALL SELECT {literal}")
        return "\n                    ".join(selects)

    def _file_path_column(self, cursor, table_name: str) -> str:
        cursor.execute(f"""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = {string_literal(normalize_identifier(self.sf_config.schema).upper())}
              AND table_name = {string_literal(table_name.upper())}
              AND UPPER(column_name) = 'FILE_PATH'
            LIMIT 1
        """)
        row = cursor.fetchone()
        if not row or not row[0]:
            raise ValueError(f"FILE_PATH column not found in Snowflake table: {self._table_ref(table_name)}")
        return self._quoted_identifier(row[0])

    def _quoted_identifier(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def _stage_name(self, table_name: str) -> str:
        return qualified_identifier(
            normalize_identifier(self.sf_config.schema),
            normalize_identifier(f"{table_name}_stage"),
            field_name="stage name",
        )

    def _temp_format_name(self, table_name: str) -> str:
        return normalize_identifier(f"temp_format_{table_name}").upper()

    def _load_rsa_key(self):
        key_path = self.sf_config.rsa_key_path
        with open(key_path, "rb") as f:
            key_data = f.read()
        try:
            return serialization.load_pem_private_key(key_data, password=None)
        except TypeError as e:
            if "password" in str(e).lower():
                passphrase = self.sf_config.rsa_key_passphrase or self._keyring_passphrase()
                return serialization.load_pem_private_key(key_data, password=passphrase.encode())
            raise

    def _keyring_passphrase(self) -> str:
        import keyring
        return keyring.get_password("SNOWFLAKE", self.sf_config.username)
