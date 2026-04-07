"""Unified pipeline configuration.

Edit this file for settings that are the same across all environments.
Put environment-specific values (credentials, ACO IDs, bucket names) in .env
— see .env.example for the full list of supported variables.

Environment variable overrides (loaded from .env, then the process environment):
  MSSP_ACO_ID              → ACO_ID
  MSSP_FILE_STORE          → FILE_STORE
  MSSP_OUTPUT_TYPE         → OUTPUT_TYPE
  MSSP_OUTPUT_LOCATION     → OUTPUT_LOCATION
  MSSP_FULL_REFRESH        → FULL_REFRESH (set to '1' or 'true' to enable)
  SNOWFLAKE_USERNAME       → SNOWFLAKE_USERNAME
  SNOWFLAKE_DATABASE       → SNOWFLAKE_DATABASE
  SNOWFLAKE_ACCOUNT        → SNOWFLAKE_ACCOUNT
  SNOWFLAKE_RSA_KEY_PATH   → RSA_KEY_PATH
  SNOWFLAKE_RSA_KEY_PASSPHRASE → RSA_KEY_PASSPHRASE
  AWS_PROFILE              → AWS_PROFILE
  AWS_REGION               → AWS_REGION
"""

import os
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
# Local path, 's3://bucket/prefix', or 'az://container/prefix'.
FILE_STORE: str = os.environ.get("MSSP_FILE_STORE", "")

# ---------------------------------------------------------------------------
# Integration (download) settings
# ---------------------------------------------------------------------------

# First performance year to download
START_YEAR: int = 2025

# 'incremental' (default) or 'full'
DOWNLOAD_MODE: str = "incremental"

# Path to the acoms-cli binary shipped with this package
CLI_PATH: Path = Path(__file__).parent.parent / "bin" / "acoms-cli"

# Local state tracking file (used when not uploading to S3)
STATE_FILE: Path = Path("state.json")

# Set to an S3 bucket name to upload extracted files there and delete local copies.
# Defaults to the bucket inferred from FILE_STORE when it is an s3:// URL.
# Override with MSSP_S3_BUCKET to use a different bucket for downloads vs. the file store.
def _s3_bucket_from_file_store(file_store: str) -> str | None:
    if file_store.startswith("s3://"):
        bucket = file_store[5:].split("/")[0]
        return bucket or None
    return None

S3_BUCKET: str | None = (
    os.environ.get("MSSP_S3_BUCKET")
    or _s3_bucket_from_file_store(FILE_STORE)
    or None
)

# ---------------------------------------------------------------------------
# Processing (ETL) settings
# ---------------------------------------------------------------------------

OUTPUT_TYPE: str = os.environ.get("MSSP_OUTPUT_TYPE", "SNOWFLAKE")

# Set to True to drop and recreate all output tables on every run.
FULL_REFRESH: bool = os.environ.get("MSSP_FULL_REFRESH", "").lower() in ("1", "true", "yes")

# REQUIRED FOR DUCKDB / PARQUET OUTPUTS
OUTPUT_LOCATION: str = os.path.expanduser(os.environ.get("MSSP_OUTPUT_LOCATION", "~/.data/mssp.duckdb"))

# MOTHERDUCK OUTPUT CONFIGURATION
MOTHERDUCK_DATABASE: str = ""
MOTHERDUCK_TOKEN: str = ""

# REQUIRED FOR SNOWFLAKE / DATABRICKS / BIGQUERY / REDSHIFT / FABRIC OUTPUTS
TEMP_LOCATION: str = "./STAGED"

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
SNOWFLAKE_SCHEMA: str = "RAW_DATA"
SNOWFLAKE_COMPUTE_WAREHOUSE: str = "COMPUTE_WH"
SNOWFLAKE_ACCOUNT_ROLE: str = "ACCOUNTADMIN"
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
DATABRICKS_SERVER_HOSTNAME: str = ""
DATABRICKS_HTTP_PATH: str = ""
DATABRICKS_ACCESS_TOKEN: str = ""
DATABRICKS_SCHEMA: str = "raw_data"
DATABRICKS_CATALOG: str = ""
DATABRICKS_STAGING_PATH: str = ""

DATABRICKS = DatabricksConfig(
    server_hostname=DATABRICKS_SERVER_HOSTNAME,
    http_path=DATABRICKS_HTTP_PATH,
    access_token=DATABRICKS_ACCESS_TOKEN,
    schema=DATABRICKS_SCHEMA,
    catalog=DATABRICKS_CATALOG,
    staging_path=DATABRICKS_STAGING_PATH,
)

# REDSHIFT OUTPUT CONFIGURATION
REDSHIFT_HOST: str = ""
REDSHIFT_DATABASE: str = ""
REDSHIFT_SCHEMA: str = "raw_data"
REDSHIFT_USER: str = ""
REDSHIFT_PASSWORD: str = ""
REDSHIFT_IAM_ROLE: str = ""
REDSHIFT_STAGING_BUCKET: str = ""
REDSHIFT_PORT: int = 5439

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
BIGQUERY_PROJECT_ID: str = ""
BIGQUERY_DATASET_ID: str = "raw_data"
BIGQUERY_STAGING_BUCKET: str = ""
BIGQUERY_CREDENTIALS_PATH: str = ""
BIGQUERY_LOCATION: str = "US"

BIGQUERY = BigQueryConfig(
    project_id=BIGQUERY_PROJECT_ID,
    dataset_id=BIGQUERY_DATASET_ID,
    staging_bucket=BIGQUERY_STAGING_BUCKET,
    credentials_path=BIGQUERY_CREDENTIALS_PATH,
    location=BIGQUERY_LOCATION,
)

# FABRIC LAKEHOUSE OUTPUT CONFIGURATION
FABRIC_ONELAKE_PATH: str = ""
FABRIC_TENANT_ID: str = ""
FABRIC_CLIENT_ID: str = ""
FABRIC_CLIENT_SECRET: str = ""

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
AZURE_STORAGE_CONNECTION_STRING: str = ""
AZURE_STORAGE_ACCOUNT: str = ""
