import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # loads .env from the project root (no-op if file absent)

from .config_defs import SnowflakeConfig, DatabricksConfig, RedshiftConfig, FabricConfig, BigQueryConfig


FILE_STORE = os.environ.get("MSSP_FILE_STORE", "")
ACO_ID = os.environ.get("MSSP_ACO_ID", "")

OUTPUT_TYPE = os.environ.get("MSSP_OUTPUT_TYPE", "SNOWFLAKE")  # PARQUET, DUCKDB, MOTHERDUCK, SNOWFLAKE, DATABRICKS, BIGQUERY, REDSHIFT, FABRIC

# Set to True to drop and recreate all output tables on every run.
# Default False = incremental mode: only rows from new source files are appended.
FULL_REFRESH = os.environ.get("MSSP_FULL_REFRESH", "").lower() in ("1", "true", "yes")

# REQUIRED FOR DUCKDB / PARQUET OUTPUTS
OUTPUT_LOCATION = os.path.expanduser(os.environ.get("MSSP_OUTPUT_LOCATION", "~/.data/mssp.duckdb"))

# MOTHERDUCK OUTPUT CONFIGURATION
MOTHERDUCK_DATABASE = os.environ.get("MOTHERDUCK_DATABASE", "")  # MotherDuck database name (the part after md:)
MOTHERDUCK_TOKEN = os.environ.get("MOTHERDUCK_TOKEN", "")     # Personal access token; empty = use motherduck_token env var

# REQUIRED FOR SNOWFLAKE / DATABRICKS / BIGQUERY OUTPUTS
TEMP_LOCATION = os.path.expanduser(os.environ.get("MSSP_TEMP_LOCATION", "./STAGED"))

# SNOWFLAKE OUTPUT CONFIGURATION
RSA_KEY_PATH = os.path.expanduser(os.environ.get(
    "SNOWFLAKE_RSA_KEY_PATH",
    str(Path.home() / ".ssh" / "snowflake_rsa_key.p8"),
))
# RSA key passphrase — set SNOWFLAKE_RSA_KEY_PASSPHRASE in .env to avoid macOS
# Keychain prompts. Leave empty to fall back to keyring (requires a stored entry
# under service "SNOWFLAKE" / username matching SNOWFLAKE_USERNAME).
RSA_KEY_PASSPHRASE = os.environ.get("SNOWFLAKE_RSA_KEY_PASSPHRASE", "")
SNOWFLAKE_USERNAME = os.environ.get("SNOWFLAKE_USERNAME", "")
SNOWFLAKE_DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "")
SNOWFLAKE_SCHEMA = os.environ.get("SNOWFLAKE_SCHEMA", "RAW_DATA")
SNOWFLAKE_COMPUTE_WAREHOUSE = os.environ.get("SNOWFLAKE_COMPUTE_WAREHOUSE", "COMPUTE_WH")
SNOWFLAKE_ACCOUNT_ROLE = os.environ.get("SNOWFLAKE_ACCOUNT_ROLE", "ACCOUNTADMIN")
SNOWFLAKE_ACCOUNT = os.environ.get("SNOWFLAKE_ACCOUNT", "")

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
DATABRICKS_SERVER_HOSTNAME = os.environ.get("DATABRICKS_SERVER_HOSTNAME", "")
DATABRICKS_HTTP_PATH = os.environ.get("DATABRICKS_HTTP_PATH", "")
DATABRICKS_ACCESS_TOKEN = os.environ.get("DATABRICKS_ACCESS_TOKEN", "")
DATABRICKS_SCHEMA = os.environ.get("DATABRICKS_SCHEMA", "raw_data")
DATABRICKS_CATALOG = os.environ.get("DATABRICKS_CATALOG", "")         # Leave empty if not using Unity Catalog
DATABRICKS_STAGING_PATH = os.environ.get("DATABRICKS_STAGING_PATH", "")    # e.g. 's3://my-bucket/mssp-staging' or 'dbfs:/tmp/mssp'

DATABRICKS = DatabricksConfig(
    server_hostname=DATABRICKS_SERVER_HOSTNAME,
    http_path=DATABRICKS_HTTP_PATH,
    access_token=DATABRICKS_ACCESS_TOKEN,
    schema=DATABRICKS_SCHEMA,
    catalog=DATABRICKS_CATALOG,
    staging_path=DATABRICKS_STAGING_PATH,
)

# REDSHIFT OUTPUT CONFIGURATION
REDSHIFT_HOST = os.environ.get("REDSHIFT_HOST", "")
REDSHIFT_DATABASE = os.environ.get("REDSHIFT_DATABASE", "")
REDSHIFT_SCHEMA = os.environ.get("REDSHIFT_SCHEMA", "raw_data")
REDSHIFT_USER = os.environ.get("REDSHIFT_USER", "")
REDSHIFT_PASSWORD = os.environ.get("REDSHIFT_PASSWORD", "")
REDSHIFT_IAM_ROLE = os.environ.get("REDSHIFT_IAM_ROLE", "")          # e.g. 'arn:aws:iam::123456789012:role/MyRedshiftRole'
REDSHIFT_STAGING_BUCKET = os.environ.get("REDSHIFT_STAGING_BUCKET", "")    # e.g. 's3://my-bucket/mssp-staging'
REDSHIFT_PORT = int(os.environ.get("REDSHIFT_PORT", "5439"))

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
BIGQUERY_PROJECT_ID = os.environ.get("BIGQUERY_PROJECT_ID", "")
BIGQUERY_DATASET_ID = os.environ.get("BIGQUERY_DATASET_ID", "raw_data")
BIGQUERY_STAGING_BUCKET = os.environ.get("BIGQUERY_STAGING_BUCKET", "")    # e.g. 'gs://my-bucket/mssp-staging'
BIGQUERY_CREDENTIALS_PATH = os.environ.get("BIGQUERY_CREDENTIALS_PATH", os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""))  # path to service account JSON; empty = Application Default Credentials
BIGQUERY_LOCATION = os.environ.get("BIGQUERY_LOCATION", "US")

BIGQUERY = BigQueryConfig(
    project_id=BIGQUERY_PROJECT_ID,
    dataset_id=BIGQUERY_DATASET_ID,
    staging_bucket=BIGQUERY_STAGING_BUCKET,
    credentials_path=BIGQUERY_CREDENTIALS_PATH,
    location=BIGQUERY_LOCATION,
)

# FABRIC LAKEHOUSE OUTPUT CONFIGURATION
FABRIC_ONELAKE_PATH = os.environ.get("FABRIC_ONELAKE_PATH", "")   # e.g. 'abfss://MyWorkspace@onelake.dfs.fabric.microsoft.com/MyLakehouse.Lakehouse/Tables'
FABRIC_TENANT_ID = os.environ.get("FABRIC_TENANT_ID", "")
FABRIC_CLIENT_ID = os.environ.get("FABRIC_CLIENT_ID", "")      # Service Principal; empty = use env/managed identity
FABRIC_CLIENT_SECRET = os.environ.get("FABRIC_CLIENT_SECRET", "")  # Service Principal secret; empty = use env/managed identity

FABRIC = FabricConfig(
    onelake_path=FABRIC_ONELAKE_PATH,
    tenant_id=FABRIC_TENANT_ID,
    client_id=FABRIC_CLIENT_ID,
    client_secret=FABRIC_CLIENT_SECRET,
)

# S3 SOURCE CONFIGURATION
# Set FILE_STORE = 's3://bucket/prefix' to read source files directly from S3.
# Credentials are loaded from the standard AWS credential chain by default:
#   env vars (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) → ~/.aws/credentials → IAM role
# Set AWS_REGION if it is not already configured in ~/.aws/config or the environment.
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
# Named profile from ~/.aws/credentials to use (e.g. 'my-profile').
# Leave empty to use the default credential chain (env vars, [default] profile, IAM role).
AWS_PROFILE = os.environ.get("AWS_PROFILE", "")
# Optional: set only if NOT using a profile or the credential chain above.
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")

# AZURE DATA LAKE STORAGE (ADLS) SOURCE CONFIGURATION
# Set FILE_STORE = 'az://container/prefix' or 'abfss://filesystem@account.dfs.core.windows.net/prefix'
# to read source files directly from Azure storage.
#
# Option A — Connection string (explicit credentials, good for dev/CI):
AZURE_STORAGE_CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
#
# Option B — Credential chain (managed identity, Azure CLI, env vars):
#   Set AZURE_STORAGE_ACCOUNT and leave AZURE_STORAGE_CONNECTION_STRING empty.
#   DuckDB will try: env vars → Azure CLI → workload identity → managed identity
AZURE_STORAGE_ACCOUNT = os.environ.get("AZURE_STORAGE_ACCOUNT", "")

# GOOGLE CLOUD STORAGE (GCS) SOURCE CONFIGURATION
# Set FILE_STORE = 'gs://bucket/prefix' to read source files directly from GCS.
# DuckDB currently uses a GCS secret backed by HMAC credentials.
GCS_PROJECT_ID = os.environ.get("GCS_PROJECT_ID", "")
GCS_CREDENTIALS_PATH = os.environ.get(
    "MSSP_GCS_CREDENTIALS_PATH",
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
)
GCS_KEY_ID = os.environ.get("GCS_KEY_ID", "")
GCS_SECRET = os.environ.get("GCS_SECRET", "")
