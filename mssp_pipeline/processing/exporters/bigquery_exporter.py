import os
from google.cloud import bigquery, storage
from google.cloud.exceptions import NotFound

from .base import normalize_identifier, normalize_query, string_literal


class BigQueryExporter:
    """
    Exports query results to BigQuery via a local Parquet staging file and GCS.

    Flow:
    - DuckDB writes Parquet locally to staging_dir
    - File is uploaded to GCS (staging_bucket)
    - BigQuery load job reads the Parquet from GCS into the target table

    Full refresh / first run: load job with WRITE_TRUNCATE (creates table if needed).
    Incremental: load job with WRITE_APPEND (query is pre-filtered by processor).
    """

    def __init__(self, bq_config, staging_dir: str, full_refresh: bool = False):
        """
        Args:
            bq_config:    A BigQueryConfig dataclass instance (from config.py).
            staging_dir:  Local directory for temporary Parquet files.
            full_refresh: If True, truncate and recreate the table on every run.
                          If False (default), only append rows from new source files.
        """
        self.bq_config = bq_config
        self.staging_dir = staging_dir
        self.full_refresh = full_refresh

    def export(self, query: str, table_name: str, duckdb_connection) -> None:
        table_name = normalize_identifier(table_name)
        query = normalize_query(query, duckdb_connection)
        local_parquet = os.path.join(self.staging_dir, f"{table_name}.parquet")
        gcs_uri = f"{self.bq_config.staging_bucket.rstrip('/')}/{table_name}.parquet"

        if os.path.exists(local_parquet):
            os.remove(local_parquet)

        table_exists = self._bigquery_table_exists(table_name)
        is_incremental = (not self.full_refresh) and table_exists

        print(f"  Writing staging Parquet: {local_parquet}")
        duckdb_connection.execute(f"COPY ({query}) TO {string_literal(local_parquet)} (FORMAT PARQUET)")

        self._upload_to_gcs(local_parquet, gcs_uri)

        if is_incremental:
            self._load_to_bigquery(gcs_uri, table_name, write_disposition='WRITE_APPEND')
        else:
            self._load_to_bigquery(gcs_uri, table_name, write_disposition='WRITE_TRUNCATE')

        if os.path.exists(local_parquet):
            os.remove(local_parquet)

    def get_existing_file_paths(self, table_name: str, duckdb_connection) -> list:
        """Return distinct FILE_PATH values from the existing BigQuery table, or [] if not found."""
        table_name = normalize_identifier(table_name)
        client = self._connect()
        try:
            rows = client.query(
                f"SELECT DISTINCT FILE_PATH FROM `{self._table_ref(table_name)}`"
            ).result()
            paths = [row[0] for row in rows]
            print(f"  Found {len(paths)} existing FILE_PATH(s) in BigQuery for {table_name}")
            return paths
        except NotFound:
            return []

    def get_missing_file_paths(self, table_name: str, candidate_file_paths: list[str], duckdb_connection) -> list[str]:
        existing = set(self.get_existing_file_paths(table_name, duckdb_connection))
        return [path for path in candidate_file_paths if path not in existing]

    def _connect(self):
        if self.bq_config.credentials_path:
            return bigquery.Client.from_service_account_json(
                self.bq_config.credentials_path,
                project=self.bq_config.project_id,
            )
        return bigquery.Client(project=self.bq_config.project_id)

    def _storage_client(self):
        if self.bq_config.credentials_path:
            return storage.Client.from_service_account_json(
                self.bq_config.credentials_path,
                project=self.bq_config.project_id,
            )
        return storage.Client(project=self.bq_config.project_id)

    def _table_ref(self, table_name: str) -> str:
        return f"{self.bq_config.project_id}.{self.bq_config.dataset_id}.{table_name}"

    def _bigquery_table_exists(self, table_name: str) -> bool:
        """Returns True if the target table already exists in BigQuery."""
        client = self._connect()
        try:
            client.get_table(self._table_ref(table_name))
            return True
        except NotFound:
            return False

    def _fetch_existing_file_paths(self, table_name: str) -> list:
        """Fetches all distinct FILE_PATH values from the existing BigQuery table."""
        client = self._connect()
        rows = client.query(
            f"SELECT DISTINCT FILE_PATH FROM `{self._table_ref(table_name)}`"
        ).result()
        return [row[0] for row in rows]

    def _upload_to_gcs(self, local_path: str, gcs_uri: str) -> None:
        """Upload a local file to GCS."""
        without_scheme = gcs_uri[len('gs://'):]
        bucket_name, _, blob_path = without_scheme.partition('/')
        print(f"  Uploading to GCS: {gcs_uri}")
        gcs_client = self._storage_client()
        gcs_client.bucket(bucket_name).blob(blob_path).upload_from_filename(local_path)

    def _load_to_bigquery(self, gcs_uri: str, table_name: str, write_disposition: str) -> None:
        """Trigger a BigQuery load job from GCS."""
        table_ref = self._table_ref(table_name)
        client = self._connect()
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=write_disposition,
            autodetect=True,
        )
        action = "Creating" if write_disposition == 'WRITE_TRUNCATE' else "Appending into"
        print(f"  {action} {table_ref}...")
        try:
            load_job = client.load_table_from_uri(
                gcs_uri,
                table_ref,
                location=self.bq_config.location,
                job_config=job_config,
            )
            load_job.result()  # blocks until complete; raises on failure
            action_done = "loaded" if write_disposition == 'WRITE_TRUNCATE' else "appended to"
            print(f"✅ Successfully {action_done} {table_ref}")
        except Exception as e:
            print(f"  Error loading {table_name} to BigQuery: {e}")
            raise
