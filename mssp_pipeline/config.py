from __future__ import annotations

"""Unified pipeline configuration.

Edit this file for settings that are the same across all environments.
Put environment-specific values (credentials, ACO IDs, bucket names) in .env
— see .env.example for the full list of supported variables.

Environment variable overrides (loaded from .env, then the process environment):
  Shared: MSSP_ACO_ID, MSSP_FILE_STORE, MSSP_OUTPUT_TYPE,
          MSSP_OUTPUT_LOCATION, MSSP_FULL_REFRESH, MSSP_TEMP_LOCATION,
          MSSP_START_YEAR, MSSP_DOWNLOAD_MODE, MSSP_S3_BUCKET
  Snowflake: SNOWFLAKE_*
  Databricks: DATABRICKS_*
  Redshift: REDSHIFT_*
  BigQuery: BIGQUERY_*
  Fabric: FABRIC_*
  MotherDuck: MOTHERDUCK_*
  AWS / Azure / GCS source settings: AWS_*, AZURE_*, GCS_*, GOOGLE_APPLICATION_CREDENTIALS
"""

import os
from dataclasses import dataclass, fields
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # loads .env from the project root (no-op if file absent)

from mssp_pipeline.processing.config_defs import (
    SnowflakeConfig,
    DatabricksConfig,
    RedshiftConfig,
    FabricConfig,
    BigQueryConfig,
)

# ---------------------------------------------------------------------------
# Shared — used by both integration (download) and processing (ETL)
# ---------------------------------------------------------------------------

ACO_ID: str = os.environ.get("MSSP_ACO_ID", "")

# Where organised ACO files are stored — the file store that both subsystems share.
# Local path, 's3://bucket/prefix', 'az://container/prefix', or 'gs://bucket/prefix'.
FILE_STORE: str = os.environ.get("MSSP_FILE_STORE", "")

# ---------------------------------------------------------------------------
# Integration (download) settings
# ---------------------------------------------------------------------------

# First performance year to download
START_YEAR: int = int(os.environ.get("MSSP_START_YEAR", "2025"))

# 'incremental' (default) or 'full'
DOWNLOAD_MODE: str = os.environ.get("MSSP_DOWNLOAD_MODE", "incremental")

# Path to the acoms-cli binary shipped with this package.
# In containers, set MSSP_CLI_PATH=/app/bin/acoms-cli when the binary is copied
# outside the installed site-packages directory.
CLI_PATH: Path = Path(os.environ.get(
    "MSSP_CLI_PATH",
    str(Path(__file__).parent.parent / "bin" / "acoms-cli"),
))

# Local state tracking file (used when not uploading to S3)
STATE_FILE: Path = Path("state.json")

# Shared remote file store for integration upload + processing reads.
REMOTE_FILE_STORE: str | None = FILE_STORE if FILE_STORE.startswith(
    ("s3://", "az://", "azure://", "abfss://", "gs://")
) else None

# Backward-compatible alias for the legacy integration-only S3 flag.
# Prefer FILE_STORE / REMOTE_FILE_STORE for new usage.
S3_BUCKET: str | None = os.environ.get("MSSP_S3_BUCKET") or None

# ---------------------------------------------------------------------------
# Processing (ETL) settings
# ---------------------------------------------------------------------------

OUTPUT_TYPE: str = os.environ.get("MSSP_OUTPUT_TYPE", "SNOWFLAKE")

# Set to True to drop and recreate all output tables on every run.
FULL_REFRESH: bool = os.environ.get("MSSP_FULL_REFRESH", "").lower() in ("1", "true", "yes")

# REQUIRED FOR DUCKDB / PARQUET OUTPUTS
OUTPUT_LOCATION: str = os.path.expanduser(os.environ.get("MSSP_OUTPUT_LOCATION", "~/.data/mssp.duckdb"))

# MOTHERDUCK OUTPUT CONFIGURATION
MOTHERDUCK_DATABASE: str = os.environ.get("MOTHERDUCK_DATABASE", "")
MOTHERDUCK_TOKEN: str = os.environ.get("MOTHERDUCK_TOKEN", "")

# REQUIRED FOR SNOWFLAKE / DATABRICKS / BIGQUERY / REDSHIFT / FABRIC OUTPUTS
TEMP_LOCATION: str = os.path.expanduser(os.environ.get("MSSP_TEMP_LOCATION", "./STAGED"))

# Processing batch sizing. These limits cap how many source files a processor
# includes in a single DuckDB query/export batch to reduce peak memory usage.
PROCESS_BATCH_SIZE_DEFAULT: int = int(os.environ.get("MSSP_PROCESS_BATCH_SIZE_DEFAULT", "25"))
PROCESS_BATCH_SIZE_CCLF: int = int(os.environ.get("MSSP_PROCESS_BATCH_SIZE_CCLF", "1"))
PROCESS_BATCH_SIZE_MSSP: int = int(os.environ.get("MSSP_PROCESS_BATCH_SIZE_MSSP", "25"))
PROCESS_BATCH_SIZE_MCQM: int = int(os.environ.get("MSSP_PROCESS_BATCH_SIZE_MCQM", "5"))
PROCESS_BATCH_SIZE_EXPU: int = int(os.environ.get("MSSP_PROCESS_BATCH_SIZE_EXPU", "2"))

# SNOWFLAKE OUTPUT CONFIGURATION
RSA_KEY_PATH: str = os.path.expanduser(os.environ.get(
    "SNOWFLAKE_RSA_KEY_PATH",
    str(Path.home() / ".ssh" / "snowflake_rsa_key.p8"),
))
# RSA key passphrase — set SNOWFLAKE_RSA_KEY_PASSPHRASE in .env to avoid macOS
# Keychain prompts. Leave empty to fall back to keyring (requires a stored entry
# under service "SNOWFLAKE" / username matching SNOWFLAKE_USERNAME).
RSA_KEY_PASSPHRASE: str = os.environ.get("SNOWFLAKE_RSA_KEY_PASSPHRASE", "")
SNOWFLAKE_USERNAME: str = os.environ.get("SNOWFLAKE_USERNAME", "")
SNOWFLAKE_DATABASE: str = os.environ.get("SNOWFLAKE_DATABASE", "")
SNOWFLAKE_SCHEMA: str = os.environ.get("SNOWFLAKE_SCHEMA", "RAW_DATA")
SNOWFLAKE_COMPUTE_WAREHOUSE: str = os.environ.get("SNOWFLAKE_COMPUTE_WAREHOUSE", "COMPUTE_WH")
SNOWFLAKE_ACCOUNT_ROLE: str = os.environ.get("SNOWFLAKE_ACCOUNT_ROLE", "ACCOUNTADMIN")
SNOWFLAKE_ACCOUNT: str = os.environ.get("SNOWFLAKE_ACCOUNT", "")

SNOWFLAKE = SnowflakeConfig(
    username=SNOWFLAKE_USERNAME,
    account=SNOWFLAKE_ACCOUNT,
    database=SNOWFLAKE_DATABASE,
    schema=SNOWFLAKE_SCHEMA,
    warehouse=SNOWFLAKE_COMPUTE_WAREHOUSE,
    role=SNOWFLAKE_ACCOUNT_ROLE,
    rsa_key_path=RSA_KEY_PATH,
    rsa_key_passphrase=RSA_KEY_PASSPHRASE,
)

# DATABRICKS OUTPUT CONFIGURATION
DATABRICKS_SERVER_HOSTNAME: str = os.environ.get("DATABRICKS_SERVER_HOSTNAME", "")
DATABRICKS_HTTP_PATH: str = os.environ.get("DATABRICKS_HTTP_PATH", "")
DATABRICKS_ACCESS_TOKEN: str = os.environ.get("DATABRICKS_ACCESS_TOKEN", "")
DATABRICKS_SCHEMA: str = os.environ.get("DATABRICKS_SCHEMA", "raw_data")
DATABRICKS_CATALOG: str = os.environ.get("DATABRICKS_CATALOG", "")
DATABRICKS_STAGING_PATH: str = os.environ.get("DATABRICKS_STAGING_PATH", "")

DATABRICKS = DatabricksConfig(
    server_hostname=DATABRICKS_SERVER_HOSTNAME,
    http_path=DATABRICKS_HTTP_PATH,
    access_token=DATABRICKS_ACCESS_TOKEN,
    schema=DATABRICKS_SCHEMA,
    catalog=DATABRICKS_CATALOG,
    staging_path=DATABRICKS_STAGING_PATH,
)

# REDSHIFT OUTPUT CONFIGURATION
REDSHIFT_HOST: str = os.environ.get("REDSHIFT_HOST", "")
REDSHIFT_DATABASE: str = os.environ.get("REDSHIFT_DATABASE", "")
REDSHIFT_SCHEMA: str = os.environ.get("REDSHIFT_SCHEMA", "raw_data")
REDSHIFT_USER: str = os.environ.get("REDSHIFT_USER", "")
REDSHIFT_PASSWORD: str = os.environ.get("REDSHIFT_PASSWORD", "")
REDSHIFT_IAM_ROLE: str = os.environ.get("REDSHIFT_IAM_ROLE", "")
REDSHIFT_STAGING_BUCKET: str = os.environ.get("REDSHIFT_STAGING_BUCKET", "")
REDSHIFT_PORT: int = int(os.environ.get("REDSHIFT_PORT", "5439"))

REDSHIFT = RedshiftConfig(
    host=REDSHIFT_HOST,
    database=REDSHIFT_DATABASE,
    schema=REDSHIFT_SCHEMA,
    user=REDSHIFT_USER,
    password=REDSHIFT_PASSWORD,
    iam_role=REDSHIFT_IAM_ROLE,
    staging_bucket=REDSHIFT_STAGING_BUCKET,
    port=REDSHIFT_PORT,
)

# BIGQUERY OUTPUT CONFIGURATION
BIGQUERY_PROJECT_ID: str = os.environ.get("BIGQUERY_PROJECT_ID", "")
BIGQUERY_DATASET_ID: str = os.environ.get("BIGQUERY_DATASET_ID", "raw_data")
BIGQUERY_STAGING_BUCKET: str = os.environ.get("BIGQUERY_STAGING_BUCKET", "")
BIGQUERY_CREDENTIALS_PATH: str = os.environ.get(
    "BIGQUERY_CREDENTIALS_PATH",
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
)
BIGQUERY_LOCATION: str = os.environ.get("BIGQUERY_LOCATION", "US")

BIGQUERY = BigQueryConfig(
    project_id=BIGQUERY_PROJECT_ID,
    dataset_id=BIGQUERY_DATASET_ID,
    staging_bucket=BIGQUERY_STAGING_BUCKET,
    credentials_path=BIGQUERY_CREDENTIALS_PATH,
    location=BIGQUERY_LOCATION,
)

# FABRIC LAKEHOUSE OUTPUT CONFIGURATION
FABRIC_ONELAKE_PATH: str = os.environ.get("FABRIC_ONELAKE_PATH", "")
FABRIC_TENANT_ID: str = os.environ.get("FABRIC_TENANT_ID", "")
FABRIC_CLIENT_ID: str = os.environ.get("FABRIC_CLIENT_ID", "")
FABRIC_CLIENT_SECRET: str = os.environ.get("FABRIC_CLIENT_SECRET", "")

FABRIC = FabricConfig(
    onelake_path=FABRIC_ONELAKE_PATH,
    tenant_id=FABRIC_TENANT_ID,
    client_id=FABRIC_CLIENT_ID,
    client_secret=FABRIC_CLIENT_SECRET,
)

# S3 SOURCE CONFIGURATION
AWS_REGION: str = os.environ.get("AWS_REGION", "us-east-1")
# Named profile from ~/.aws/credentials to use (e.g. 'my-profile').
# Leave empty to use the default credential chain (env vars, [default] profile, IAM role).
AWS_PROFILE: str = os.environ.get("AWS_PROFILE", "")
# Optional: set only if NOT using a profile or the credential chain above.
AWS_ACCESS_KEY_ID: str = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY: str = os.environ.get("AWS_SECRET_ACCESS_KEY", "")

# AZURE DATA LAKE STORAGE (ADLS) SOURCE CONFIGURATION
AZURE_STORAGE_CONNECTION_STRING: str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
AZURE_STORAGE_ACCOUNT: str = os.environ.get("AZURE_STORAGE_ACCOUNT", "")

# GOOGLE CLOUD STORAGE (GCS) SOURCE CONFIGURATION
# Set FILE_STORE = 'gs://bucket/prefix' to read source files directly from GCS.
# DuckDB httpfs uses GCS HMAC credentials rather than service-account JSON.
GCS_PROJECT_ID: str = os.environ.get("GCS_PROJECT_ID", "")
GCS_CREDENTIALS_PATH: str = os.environ.get(
    "MSSP_GCS_CREDENTIALS_PATH",
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
)
GCS_KEY_ID: str = os.environ.get("GCS_KEY_ID", "")
GCS_SECRET: str = os.environ.get("GCS_SECRET", "")


@dataclass
class RuntimeConfig:
    """Typed runtime configuration shared by integration + processing."""

    # Shared
    ACO_ID: str
    FILE_STORE: str

    # Integration
    START_YEAR: int
    DOWNLOAD_MODE: str
    CLI_PATH: Path
    STATE_FILE: Path
    REMOTE_FILE_STORE: str | None
    S3_BUCKET: str | None

    # Processing
    OUTPUT_TYPE: str
    FULL_REFRESH: bool
    OUTPUT_LOCATION: str
    MOTHERDUCK_DATABASE: str
    MOTHERDUCK_TOKEN: str
    TEMP_LOCATION: str
    PROCESS_BATCH_SIZE_DEFAULT: int
    PROCESS_BATCH_SIZE_CCLF: int
    PROCESS_BATCH_SIZE_MSSP: int
    PROCESS_BATCH_SIZE_MCQM: int
    PROCESS_BATCH_SIZE_EXPU: int

    # Backend configs
    SNOWFLAKE: SnowflakeConfig
    DATABRICKS: DatabricksConfig
    REDSHIFT: RedshiftConfig
    BIGQUERY: BigQueryConfig
    FABRIC: FabricConfig

    # Source access
    AWS_REGION: str
    AWS_PROFILE: str
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str
    AZURE_STORAGE_CONNECTION_STRING: str
    AZURE_STORAGE_ACCOUNT: str
    GCS_PROJECT_ID: str
    GCS_CREDENTIALS_PATH: str
    GCS_KEY_ID: str
    GCS_SECRET: str


def runtime_values() -> dict[str, object]:
    """Return config values that map directly to RuntimeConfig fields."""
    names = {f.name for f in fields(RuntimeConfig)}
    return {name: globals()[name] for name in names}


def runtime_config(**overrides: object) -> RuntimeConfig:
    """Build a mutable typed runtime config with optional overrides."""
    values = runtime_values()
    values.update({k: v for k, v in overrides.items() if v is not None})
    return RuntimeConfig(**values)
