import os
import boto3
import redshift_connector

from .base import normalize_identifier, normalize_query, qualified_identifier, string_literal


class RedshiftExporter:
    """
    Exports query results to Redshift via a local Parquet staging file and S3.

    Flow:
    - DuckDB writes Parquet locally to staging_dir
    - boto3 uploads the file to S3 (staging_bucket)
    - Redshift COPY command reads from S3 into the target table

    Full refresh / first run: DuckDB DESCRIBE → DROP + CREATE TABLE (all VARCHAR) → COPY.
    Incremental: COPY with WRITE_APPEND (query is pre-filtered by processor).

    The IAM role (`iam_role`) must be attached to the Redshift cluster and grant it
    read access to `staging_bucket`. boto3 S3 writes use the standard AWS credential
    chain (env vars → ~/.aws/credentials → IAM instance role).
    """

    def __init__(self, rs_config, staging_dir: str, full_refresh: bool = False):
        """
        Args:
            rs_config:    A RedshiftConfig dataclass instance (from config.py).
            staging_dir:  Local directory for temporary Parquet files.
            full_refresh: If True, drop and recreate the table on every run.
                          If False (default), only append rows from new source files.
        """
        self.rs_config = rs_config
        self.staging_dir = staging_dir
        self.full_refresh = full_refresh

    def export(self, query: str, table_name: str, duckdb_connection) -> None:
        table_name = normalize_identifier(table_name)
        query = normalize_query(query, duckdb_connection)
        local_parquet = os.path.join(self.staging_dir, f"{table_name}.parquet")
        s3_uri = f"{self.rs_config.staging_bucket.rstrip('/')}/{table_name}.parquet"

        if os.path.exists(local_parquet):
            os.remove(local_parquet)

        table_exists = self._redshift_table_exists(table_name)
        is_incremental = (not self.full_refresh) and table_exists

        print(f"  Writing staging Parquet: {local_parquet}")
        duckdb_connection.execute(f"COPY ({query}) TO {string_literal(local_parquet)} (FORMAT PARQUET)")

        if not is_incremental:
            self._create_table(table_name, duckdb_connection, query)

        self._upload_to_s3(local_parquet, s3_uri)
        self._copy_from_s3(s3_uri, table_name)

        if os.path.exists(local_parquet):
            os.remove(local_parquet)

    def get_existing_file_paths(self, table_name: str, duckdb_connection) -> list:
        """Return distinct FILE_PATH values from the existing Redshift table, or [] if not found."""
        table_name = normalize_identifier(table_name)
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT DISTINCT FILE_PATH FROM {self._table_ref(table_name)}")
            paths = [row[0] for row in cursor.fetchall()]
            print(f"  Found {len(paths)} existing FILE_PATH(s) in Redshift for {table_name}")
            return paths
        except Exception as e:
            if "does not exist" in str(e).lower() or "relation" in str(e).lower():
                return []
            raise
        finally:
            cursor.close()
            conn.close()

    def get_missing_file_paths(self, table_name: str, candidate_file_paths: list[str], duckdb_connection) -> list[str]:
        existing = set(self.get_existing_file_paths(table_name, duckdb_connection))
        return [path for path in candidate_file_paths if path not in existing]

    def _connect(self):
        return redshift_connector.connect(
            host=self.rs_config.host,
            database=self.rs_config.database,
            user=self.rs_config.user,
            password=self.rs_config.password,
            port=self.rs_config.port,
        )

    def _table_ref(self, table_name: str) -> str:
        return qualified_identifier(
            normalize_identifier(self.rs_config.schema),
            table_name,
            field_name="table reference",
        )

    def _redshift_table_exists(self, table_name: str) -> bool:
        """Returns True if the target table already exists in Redshift."""
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute(f"""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = {string_literal(normalize_identifier(self.rs_config.schema))}
                  AND table_name   = {string_literal(table_name)}
            """)
            return cursor.fetchone()[0] > 0
        finally:
            cursor.close()
            conn.close()

    def _fetch_existing_file_paths(self, table_name: str) -> list:
        """Fetches all distinct FILE_PATH values from the existing Redshift table."""
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT DISTINCT FILE_PATH FROM {self._table_ref(table_name)}")
            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    def _upload_to_s3(self, local_path: str, s3_uri: str) -> None:
        """Upload a local file to S3 using boto3's standard credential chain."""
        without_scheme = s3_uri[len('s3://'):]
        bucket, _, key = without_scheme.partition('/')
        print(f"  Uploading to S3: {s3_uri}")
        boto3.client('s3').upload_file(local_path, bucket, key)

    def _create_table(self, table_name: str, duckdb_connection, query: str) -> None:
        """Full refresh: drop and recreate the table using DuckDB DESCRIBE for column names."""
        cols = duckdb_connection.execute(
            f"DESCRIBE SELECT * FROM ({query}) AS q"
        ).fetchall()
        col_defs = ", ".join([f"{normalize_identifier(c[0])} VARCHAR" for c in cols])
        table_ref = self._table_ref(table_name)
        conn = self._connect()
        cursor = conn.cursor()
        try:
            print(f"  Creating table {table_ref}...")
            cursor.execute(f"DROP TABLE IF EXISTS {table_ref}")
            cursor.execute(f"CREATE TABLE {table_ref} ({col_defs})")
            conn.commit()
        except Exception as e:
            print(f"  Error creating table {table_name}: {e}")
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    def _copy_from_s3(self, s3_uri: str, table_name: str) -> None:
        """Load Parquet from S3 into the Redshift table via COPY (always appends)."""
        table_ref = self._table_ref(table_name)
        conn = self._connect()
        cursor = conn.cursor()
        try:
            print(f"  Loading into {table_ref}...")
            cursor.execute(f"""
                COPY {table_ref}
                FROM {string_literal(s3_uri)}
                IAM_ROLE {string_literal(self.rs_config.iam_role)}
                FORMAT AS PARQUET
            """)
            conn.commit()
            print(f"✅ Successfully loaded {table_ref}")
        except Exception as e:
            print(f"  Error loading {table_name} to Redshift: {e}")
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()
