import os
from databricks import sql as databricks_sql

from .base import normalize_identifier, normalize_query


class DatabricksExporter:
    """
    Exports query results to Databricks Delta tables via a Parquet staging file.

    Staging path controls where the intermediate Parquet lands:
    - s3:// or abfss:// / az://: DuckDB writes directly using its already-configured
      cloud credentials (httpfs/azure extensions). No local file or SDK upload needed.
    - dbfs:/: DuckDB writes locally to staging_dir, then uploads via databricks-sdk.

    In both cases Databricks SQL reads the staging file via parquet.`path`.
    """

    def __init__(self, db_config, staging_dir: str, full_refresh: bool = False):
        """
        Args:
            db_config:    A DatabricksConfig dataclass instance (from config.py).
            staging_dir:  Local directory for temporary Parquet files (DBFS flow only).
            full_refresh: If True, drop and recreate the table on every run.
                          If False (default), only append rows from new source files.
        """
        self.db_config = db_config
        self.staging_dir = staging_dir
        self.full_refresh = full_refresh

    def export(self, query: str, table_name: str, duckdb_connection) -> None:
        table_name = normalize_identifier(table_name)
        query = normalize_query(query, duckdb_connection)
        staging_path = self.db_config.staging_path
        parquet_name = f"{table_name}.parquet"
        is_cloud = staging_path.startswith(('s3://', 'abfss://', 'az://'))

        # final_parquet: path Databricks SQL reads from (cloud or DBFS)
        # local_parquet: local staging file path (DBFS flow only, else None)
        final_parquet = f"{staging_path.rstrip('/')}/{parquet_name}"
        if is_cloud:
            local_parquet = None
        else:
            local_parquet = os.path.join(self.staging_dir, parquet_name)
            if os.path.exists(local_parquet):
                os.remove(local_parquet)

        table_exists = self._databricks_table_exists(table_name)
        is_incremental = (not self.full_refresh) and table_exists

        self._write_staging(duckdb_connection, query, local_parquet, final_parquet)

        if is_incremental:
            self._append_to_databricks(final_parquet, table_name)
        else:
            self._upload_to_databricks(final_parquet, table_name)

        # Clean up DBFS staging file after use.
        # Cloud staging files are left in place — they are overwritten on the next run.
        if local_parquet and os.path.exists(local_parquet):
            os.remove(local_parquet)
        if not is_cloud:
            self._dbfs_delete(final_parquet)

    def get_existing_file_paths(self, table_name: str, duckdb_connection) -> list:
        """Return distinct FILE_PATH values from the existing Databricks table, or [] if not found."""
        table_name = normalize_identifier(table_name)
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT DISTINCT FILE_PATH FROM {self._table_ref(table_name)}")
            paths = [row[0] for row in cursor.fetchall()]
            print(f"  Found {len(paths)} existing FILE_PATH(s) in Databricks for {table_name}")
            return paths
        except Exception as e:
            if "does not exist" in str(e).lower() or "table or view not found" in str(e).lower():
                return []
            raise
        finally:
            cursor.close()
            conn.close()

    def _write_staging(self, duckdb_connection, query, local_parquet, final_parquet):
        """Write staging Parquet — directly to cloud, or locally before DBFS upload."""
        if local_parquet is None:
            print(f"  Writing staging Parquet: {final_parquet}")
            duckdb_connection.execute(f"COPY ({query}) TO '{final_parquet}' (FORMAT PARQUET)")
        else:
            print(f"  Writing staging Parquet: {local_parquet}")
            duckdb_connection.execute(f"COPY ({query}) TO '{local_parquet}' (FORMAT PARQUET)")
            self._dbfs_upload(local_parquet, final_parquet)

    def _connect(self):
        return databricks_sql.connect(
            server_hostname=self.db_config.server_hostname,
            http_path=self.db_config.http_path,
            access_token=self.db_config.access_token,
        )

    def _table_ref(self, table_name: str) -> str:
        if self.db_config.catalog:
            return f"`{self.db_config.catalog}`.`{self.db_config.schema}`.`{table_name}`"
        return f"`{self.db_config.schema}`.`{table_name}`"

    def _databricks_table_exists(self, table_name: str) -> bool:
        """Returns True if the target table already exists in Databricks."""
        if self.db_config.catalog:
            info_schema = f"`{self.db_config.catalog}`.information_schema.tables"
        else:
            info_schema = "information_schema.tables"
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute(f"""
                SELECT COUNT(*) FROM {info_schema}
                WHERE table_schema = '{self.db_config.schema}'
                  AND table_name = '{table_name}'
            """)
            return cursor.fetchone()[0] > 0
        finally:
            cursor.close()
            conn.close()

    def _fetch_existing_file_paths(self, table_name: str) -> list:
        """Fetches all distinct FILE_PATH values from the existing Databricks table."""
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute(f"SELECT DISTINCT FILE_PATH FROM {self._table_ref(table_name)}")
            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    def _upload_to_databricks(self, staging_parquet: str, table_name: str) -> None:
        """Full refresh: drop and recreate the Delta table from the staging Parquet."""
        table_ref = self._table_ref(table_name)
        conn = self._connect()
        cursor = conn.cursor()
        try:
            print(f"  Creating table {table_ref}...")
            cursor.execute(f"DROP TABLE IF EXISTS {table_ref}")
            cursor.execute(f"""
                CREATE TABLE {table_ref}
                USING DELTA
                AS SELECT * FROM parquet.`{staging_parquet}`
            """)
            print(f"✅ Successfully loaded {table_ref}")
        except Exception as e:
            print(f"  Error loading {table_name} to Databricks: {e}")
        finally:
            cursor.close()
            conn.close()

    def _append_to_databricks(self, staging_parquet: str, table_name: str) -> None:
        """Incremental append: COPY INTO the existing Delta table from the staging Parquet."""
        table_ref = self._table_ref(table_name)
        conn = self._connect()
        cursor = conn.cursor()
        try:
            print(f"  Appending into {table_ref}...")
            cursor.execute(f"""
                COPY INTO {table_ref}
                FROM parquet.`{staging_parquet}`
                FILEFORMAT = PARQUET
            """)
            print(f"✅ Successfully appended to {table_ref}")
        except Exception as e:
            print(f"  Error appending {table_name} to Databricks: {e}")
        finally:
            cursor.close()
            conn.close()

    def _dbfs_upload(self, local_path: str, dbfs_path: str) -> None:
        from databricks.sdk import WorkspaceClient
        print(f"  Uploading to DBFS: {dbfs_path}")
        w = WorkspaceClient(
            host=self.db_config.server_hostname,
            token=self.db_config.access_token,
        )
        with open(local_path, 'rb') as f:
            w.dbfs.upload(dbfs_path, f, overwrite=True)

    def _dbfs_delete(self, dbfs_path: str) -> None:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient(
            host=self.db_config.server_hostname,
            token=self.db_config.access_token,
        )
        w.dbfs.delete(dbfs_path)
