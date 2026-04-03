import os


class FabricExporter:
    """
    Exports query results to a Microsoft Fabric Lakehouse as Delta tables on OneLake.

    Flow:
    - DuckDB writes Parquet locally to staging_dir
    - PyArrow reads the local Parquet into memory
    - deltalake.write_deltalake() writes Delta format directly to the OneLake ABFSS path

    No staging bucket is required — write_deltalake() streams directly to OneLake.

    Full refresh / first run: write_deltalake with mode='overwrite'.
    Incremental: write_deltalake with mode='append' (query is pre-filtered by processor).

    Authentication:
    - Service Principal: set client_id + client_secret in FabricConfig.
    - Managed identity / Azure CLI / env vars: leave client_id and client_secret empty.

    The Service Principal (or managed identity) must have Contributor access to the
    OneLake ABFSS path (workspace-level or lakehouse-level).
    """

    def __init__(self, fabric_config, staging_dir: str, full_refresh: bool = False):
        """
        Args:
            fabric_config: A FabricConfig dataclass instance (from config.py).
            staging_dir:   Local directory for temporary Parquet files.
            full_refresh:  If True, overwrite the Delta table on every run.
                           If False (default), only append rows from new source files.
        """
        self.fabric_config = fabric_config
        self.staging_dir = staging_dir
        self.full_refresh = full_refresh

    def export(self, query: str, table_name: str, duckdb_connection) -> None:
        local_parquet = os.path.join(self.staging_dir, f"{table_name}.parquet")

        if os.path.exists(local_parquet):
            os.remove(local_parquet)

        table_exists = self._fabric_table_exists(table_name)
        is_incremental = (not self.full_refresh) and table_exists

        print(f"  Writing staging Parquet: {local_parquet}")
        duckdb_connection.execute(f"COPY ({query}) TO '{local_parquet}' (FORMAT PARQUET)")

        if is_incremental:
            self._write_delta(local_parquet, table_name, mode='append')
        else:
            self._write_delta(local_parquet, table_name, mode='overwrite')

        if os.path.exists(local_parquet):
            os.remove(local_parquet)

    def get_existing_file_paths(self, table_name: str, duckdb_connection) -> list:
        """Return distinct FILE_PATH values from the existing Delta table, or [] if not found."""
        from deltalake import DeltaTable
        try:
            dt = DeltaTable(self._table_path(table_name), storage_options=self._storage_options())
            paths = (
                dt.to_pyarrow_dataset()
                  .to_table(columns=['FILE_PATH'])
                  .to_pydict()['FILE_PATH']
            )
            print(f"  Found {len(paths)} existing FILE_PATH(s) in Fabric for {table_name}")
            return paths
        except Exception as e:
            if "not a delta table" in str(e).lower() or "does not exist" in str(e).lower():
                return []
            raise

    def _table_path(self, table_name: str) -> str:
        return f"{self.fabric_config.onelake_path.rstrip('/')}/{table_name}"

    def _storage_options(self) -> dict:
        if self.fabric_config.client_id and self.fabric_config.client_secret:
            return {
                'azure_tenant_id': self.fabric_config.tenant_id,
                'azure_client_id': self.fabric_config.client_id,
                'azure_client_secret': self.fabric_config.client_secret,
            }
        return {}

    def _fabric_table_exists(self, table_name: str) -> bool:
        """Returns True if a Delta table already exists at the OneLake path."""
        from deltalake import DeltaTable
        return DeltaTable.is_deltatable(
            self._table_path(table_name),
            storage_options=self._storage_options(),
        )

    def _fetch_existing_file_paths(self, table_name: str) -> list:
        """Fetches all distinct FILE_PATH values from the existing Delta table."""
        from deltalake import DeltaTable
        dt = DeltaTable(self._table_path(table_name), storage_options=self._storage_options())
        return (
            dt.to_pyarrow_dataset()
              .to_table(columns=['FILE_PATH'])
              .to_pydict()['FILE_PATH']
        )

    def _write_delta(self, local_parquet: str, table_name: str, mode: str) -> None:
        """Read local Parquet into PyArrow and write as Delta to OneLake."""
        import pyarrow.parquet as pq
        from deltalake import write_deltalake
        table_path = self._table_path(table_name)
        action = "Creating" if mode == 'overwrite' else "Appending into"
        print(f"  {action} Delta table: {table_path}")
        pa_table = pq.read_table(local_parquet)
        try:
            write_deltalake(
                table_path,
                pa_table,
                mode=mode,
                storage_options=self._storage_options(),
            )
            action_done = "loaded" if mode == 'overwrite' else "appended to"
            print(f"✅ Successfully {action_done} {table_path}")
        except Exception as e:
            print(f"  Error writing {table_name} to Fabric Lakehouse: {e}")
