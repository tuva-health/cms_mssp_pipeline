import duckdb
import os


class DuckDBSession:
    """
    Owns the single DuckDB connection for the pipeline run.
    """

    def __init__(self, config):
        self._config = config
        self.connection = self._connect(config)
        self._configure_runtime_storage()
        self._load_extensions()

    def _connect(self, config) -> duckdb.DuckDBPyConnection:
        output_type = config.OUTPUT_TYPE
        if output_type == "PARQUET":
            os.makedirs(config.OUTPUT_LOCATION, exist_ok=True)
            return duckdb.connect(":memory:")
        elif output_type == "DUCKDB":
            return duckdb.connect(database=config.OUTPUT_LOCATION)
        elif output_type == "MOTHERDUCK":
            if config.MOTHERDUCK_TOKEN:
                return duckdb.connect(
                    f"md:{config.MOTHERDUCK_DATABASE}",
                    config={"motherduck_token": config.MOTHERDUCK_TOKEN},
                )
            return duckdb.connect(f"md:{config.MOTHERDUCK_DATABASE}")
        elif output_type in (
            "SNOWFLAKE",
            "DATABRICKS",
            "BIGQUERY",
            "REDSHIFT",
            "FABRIC",
        ):
            os.makedirs(config.TEMP_LOCATION, exist_ok=True)
            return duckdb.connect(":memory:")
        else:
            raise ValueError(
                f"config.OUTPUT_TYPE must be PARQUET, DUCKDB, MOTHERDUCK, SNOWFLAKE, DATABRICKS, BIGQUERY, REDSHIFT, or FABRIC, got: {output_type}"
            )

    def _configure_runtime_storage(self) -> None:
        """Configure DuckDB temporary spill storage for local writable scratch paths."""
        temp_location = getattr(self._config, "TEMP_LOCATION", "")
        if not temp_location:
            return
        # DuckDB temp_directory must point to a local filesystem path. Keep cloud
        # output locations out of this setting, but allow local staging/scratch dirs
        # such as /tmp/mssp-staging in ECS/Fargate.
        if temp_location.startswith(("s3://", "az://", "azure://", "abfss://", "gs://")):
            return
        os.makedirs(temp_location, exist_ok=True)
        escaped = temp_location.replace("'", "''")
        self.connection.execute(f"SET temp_directory = '{escaped}';")

    def _load_extensions(self):
        self.connection.execute(
            "INSTALL zipfs FROM community; LOAD zipfs; SET zipfs_split = '!';"
        )
        self.connection.execute("INSTALL webbed FROM community; LOAD webbed;")
        self.connection.execute("INSTALL excel; LOAD excel;")
        self.connection.execute("INSTALL rusty_sheet FROM community; LOAD rusty_sheet;")
        if self._config.FILE_STORE.startswith("s3://"):
            self._configure_s3()
        elif self._config.FILE_STORE.startswith("gs://"):
            self._configure_gcs()
        elif self._config.FILE_STORE.startswith(("az://", "azure://", "abfss://")):
            self._configure_azure()

    def _configure_s3(self):
        # httpfs gives DuckDB (and zipfs) access to S3 paths.
        self.connection.execute("INSTALL httpfs FROM core; LOAD httpfs;")
        # aws extension loads credentials from env vars, ~/.aws/credentials, or IAM role.
        self.connection.execute("INSTALL aws FROM core; LOAD aws;")

        # CCLF bundles can be several hundred MB.  The default 30-second HTTP
        # timeout is too short for large files on a slow or congested connection.
        # Five minutes covers even the largest yearly bundles, and three retries
        # with exponential back-off handle transient S3 hiccups without failing
        # the whole run.
        self.connection.execute("SET http_timeout = 300000;")       # 5 minutes (ms)
        self.connection.execute("SET http_retries = 3;")            # retry up to 3×
        self.connection.execute("SET http_retry_wait_ms = 500;")    # start at 500 ms
        self.connection.execute("SET http_retry_backoff = 4;")      # 500 → 2000 → 8000 ms

        profile = getattr(self._config, "AWS_PROFILE", "")
        if profile:
            self.connection.execute(f"CALL load_aws_credentials('{profile}');")
        else:
            self.connection.execute("CALL load_aws_credentials();")

        region = self._config.AWS_REGION or "us-east-1"

        if self._config.AWS_REGION:
            self.connection.execute(f"SET s3_region = '{self._config.AWS_REGION}';")

        # Explicit key override — only applied when set in config (non-empty string).
        # Prefer leaving these blank and using a profile or the credential chain above.
        if self._config.AWS_ACCESS_KEY_ID:
            key_id = self._config.AWS_ACCESS_KEY_ID
            secret = self._config.AWS_SECRET_ACCESS_KEY
            self.connection.execute(f"SET s3_access_key_id = '{key_id}';")
            self.connection.execute(f"SET s3_secret_access_key = '{secret}';")
            self.connection.execute(f"""
                CREATE OR REPLACE SECRET s3_credentials (
                    TYPE S3,
                    KEY_ID '{key_id}',
                    SECRET '{secret}',
                    REGION '{region}'
                )
            """)
        else:
            # Try credential chain first (preferred — picks up env vars, ~/.aws, IAM role).
            # DuckDB 1.2+ validates at CREATE time; if it fails (e.g. no [default] profile),
            # fall back to an explicit secret built from the credentials load_aws_credentials()
            # already loaded into the s3_* settings.
            try:
                self.connection.execute(f"""
                    CREATE OR REPLACE SECRET s3_credentials (
                        TYPE S3,
                        PROVIDER CREDENTIAL_CHAIN,
                        REGION '{region}'
                    )
                """)
            except Exception:
                # load_aws_credentials() already ran and populated the s3_* settings.
                # Read those values back out and create an explicit secret so that
                # community extensions (zipfs, rusty_sheet) also get S3 access.
                row = self.connection.execute("""
                    SELECT
                        (SELECT value FROM duckdb_settings() WHERE name = 's3_access_key_id'),
                        (SELECT value FROM duckdb_settings() WHERE name = 's3_secret_access_key')
                """).fetchone()
                key_id, secret = row if row else (None, None)
                if key_id and secret:
                    self.connection.execute(f"""
                        CREATE OR REPLACE SECRET s3_credentials (
                            TYPE S3,
                            KEY_ID '{key_id}',
                            SECRET '{secret}',
                            REGION '{region}'
                        )
                    """)
                else:
                    raise RuntimeError(
                        "S3 credentials could not be resolved. Set AWS_ACCESS_KEY_ID and "
                        "AWS_SECRET_ACCESS_KEY in .env, the environment, or config.py, "
                        "or ensure ~/.aws/credentials has a [default] profile."
                    )

        # Some community extensions (e.g. rusty_sheet) use the AWS Rust SDK directly
        # and read credentials from environment variables rather than DuckDB's secret
        # manager. Mirror the resolved credentials into the process environment so all
        # extensions see the same credentials regardless of how they source them.
        self._sync_credentials_to_env(region, profile)

    def _sync_credentials_to_env(self, region: str, profile: str) -> None:
        """Mirror resolved S3 credentials into os.environ.

        Community extensions such as rusty_sheet use the AWS Rust SDK directly and
        resolve credentials from environment variables rather than DuckDB's secret
        manager or the legacy s3_* settings.  After load_aws_credentials() and the
        CREATE SECRET steps have run, the resolved key/secret are available in
        DuckDB's settings.  Reading them back here and writing them into the process
        environment ensures every extension sees the same credentials.
        """
        # Always export the region — the Rust SDK won't fall back to us-east-1 if it
        # is missing, and it will refuse requests that land on the wrong endpoint.
        os.environ.setdefault("AWS_DEFAULT_REGION", region)
        os.environ.setdefault("AWS_REGION", region)

        # If the caller already set explicit key/secret in the environment (e.g. CI),
        # leave them alone — they are already visible to the Rust SDK.
        if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
            return

        # load_aws_credentials() (called earlier in _configure_s3) resolved the
        # credentials — whether from a named profile, env vars, or the credential
        # chain — and stored them in DuckDB's s3_* legacy settings.  Read them back
        # out and inject them directly into os.environ so that community extensions
        # such as rusty_sheet (which use the AWS Rust SDK and ignore DuckDB's secret
        # manager) see the same credentials.  We always do this, even when a named
        # profile was configured, because some Rust SDK builds do not honour
        # AWS_PROFILE and require explicit key/secret env vars.
        row = self.connection.execute("""
            SELECT
                (SELECT value FROM duckdb_settings() WHERE name = 's3_access_key_id'),
                (SELECT value FROM duckdb_settings() WHERE name = 's3_secret_access_key'),
                (SELECT value FROM duckdb_settings() WHERE name = 's3_session_token')
        """).fetchone()

        if row:
            key_id, secret, token = row
            if key_id:
                os.environ["AWS_ACCESS_KEY_ID"] = key_id
            if secret:
                os.environ["AWS_SECRET_ACCESS_KEY"] = secret
            if token:
                os.environ["AWS_SESSION_TOKEN"] = token

    def _configure_azure(self):
        self.connection.execute("INSTALL azure FROM core; LOAD azure;")

        if self._config.AZURE_STORAGE_CONNECTION_STRING:
            # Explicit connection string — covers account keys, SAS tokens, etc.
            conn_str = self._config.AZURE_STORAGE_CONNECTION_STRING
            self.connection.execute(f"""
                CREATE SECRET azure_secret (
                    TYPE AZURE,
                    CONNECTION_STRING '{conn_str}'
                )
            """)
        elif self._config.AZURE_STORAGE_ACCOUNT:
            # Credential chain: env vars → Azure CLI → workload identity → managed identity
            account = self._config.AZURE_STORAGE_ACCOUNT
            self.connection.execute(f"""
                CREATE SECRET azure_secret (
                    TYPE AZURE,
                    PROVIDER CREDENTIAL_CHAIN,
                    ACCOUNT_NAME '{account}'
                )
            """)
        else:
            raise ValueError(
                "ADLS source requires AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT in .env, the environment, or config"
            )

    def _configure_gcs(self):
        self.connection.execute("INSTALL httpfs FROM core; LOAD httpfs;")

        credentials_path = getattr(self._config, "GCS_CREDENTIALS_PATH", "")
        if credentials_path:
            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", credentials_path)

        key_id = getattr(self._config, "GCS_KEY_ID", "")
        secret = getattr(self._config, "GCS_SECRET", "")
        if not key_id or not secret:
            raise ValueError(
                "GCS source requires GCS_KEY_ID and GCS_SECRET in .env, the environment, or config"
            )

        project_id = getattr(self._config, "GCS_PROJECT_ID", "")
        project_clause = f", PROJECT_ID '{project_id}'" if project_id else ""
        self.connection.execute(f"""
            CREATE OR REPLACE SECRET gcs_credentials (
                TYPE GCS,
                KEY_ID '{key_id}',
                SECRET '{secret}'{project_clause}
            )
        """)

    def close(self):
        self.connection.close()
