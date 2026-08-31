"""Remote object store helpers for integration uploads and state."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


# The remote store schemes the pipeline understands. Anything else is rejected
# rather than silently treated as a local path or an unsupported backend.
REMOTE_STORE_SCHEMES = ("s3", "az", "azure", "abfss", "gs")
REMOTE_STORE_PREFIXES = tuple(f"{scheme}://" for scheme in REMOTE_STORE_SCHEMES)

# DuckDB's glob() treats these as wildcards. A source value carrying one would
# broaden or redirect a listing, so they are never allowed in a path-spliced
# identity or location.
DUCKDB_GLOB_OPERATORS = "*?[]"

# A source identifier is spliced verbatim into filesystem paths and DuckDB glob
# patterns on both the ingest and read sides. Only safe, non-empty composition
# is enforced here: letters, digits, and the punctuation that appears in real
# CMS identifiers, with no path separators, whitespace, quotes, or glob
# operators. This is deliberately weaker than any single client's format — a
# stricter per-client rule is downstream policy, not a generic invariant.
_SAFE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9._-]+")


def contains_duckdb_glob_operator(path: str) -> bool:
    return any(operator in path for operator in DUCKDB_GLOB_OPERATORS)


def validate_source_identifier(value, *, field_name: str = "source identifier") -> str:
    """Return ``value`` when it is safe to splice into a path/glob, else raise.

    Rejects empty or whitespace-only values, path separators, ``.``/``..``
    traversal segments, DuckDB glob operators, whitespace, and quotes.
    """
    if value is None or not str(value).strip():
        raise ValueError(
            f"{field_name} must be a non-empty value with no surrounding whitespace"
        )
    text = str(value)
    if text in {".", ".."}:
        raise ValueError(f"{field_name} must not be a path traversal segment: {value!r}")
    if not _SAFE_IDENTIFIER_PATTERN.fullmatch(text):
        raise ValueError(
            f"{field_name} contains unsafe characters: {value!r}. Allowed: letters, "
            f"digits, '.', '-', '_' — with no path separators, whitespace, quotes, "
            f"or glob operators"
        )
    return text


@dataclass(frozen=True)
class RemoteLocation:
    scheme: str
    bucket: str
    prefix: str
    account_name: str | None = None

    @property
    def uri(self) -> str:
        if self.scheme == "abfss":
            account = self.account_name or ""
            base = f"abfss://{self.bucket}@{account}.dfs.core.windows.net"
            return f"{base}/{self.prefix}" if self.prefix else base
        base = f"{self.scheme}://{self.bucket}"
        return f"{base}/{self.prefix}" if self.prefix else base

    def join(self, relative_path: str) -> "RemoteLocation":
        relative = relative_path.strip("/")
        prefix = "/".join(part for part in (self.prefix.strip("/"), relative) if part)
        return RemoteLocation(
            scheme=self.scheme,
            bucket=self.bucket,
            prefix=prefix,
            account_name=self.account_name,
        )


def is_remote_store(path: str | None) -> bool:
    return bool(path and path.startswith(REMOTE_STORE_PREFIXES))


def _validate_remote_path(uri: str, path: str) -> str:
    """Return the safe prefix for ``path``, or raise for a location that could
    escape or broaden its intended prefix.

    Glob operators, empty path segments (a trailing or interior ``/``), and
    ``.``/``..`` traversal segments are all rejected. The glob check runs on the
    raw URI slice because urlsplit does not decode ``[`` / ``]`` consistently.
    """
    raw_prefix = uri.split("://", maxsplit=1)[1].partition("/")[2]
    if contains_duckdb_glob_operator(raw_prefix):
        raise ValueError(f"Remote store URI contains a DuckDB glob operator: {uri}")
    if path.endswith("/") or "//" in path:
        raise ValueError(f"Remote store URI contains an empty path segment: {uri}")
    prefix = path.lstrip("/")
    if any(segment in {".", ".."} for segment in prefix.split("/")):
        raise ValueError(f"Remote store URI contains a dot path segment: {uri}")
    return prefix


def parse_remote_store(uri: str) -> RemoteLocation:
    raw_authority = uri.partition("://")[2].partition("/")[0]
    if contains_duckdb_glob_operator(raw_authority):
        raise ValueError(
            f"Remote store URI authority contains a DuckDB glob operator: {uri}"
        )

    parsed = urlsplit(uri)
    if parsed.query or parsed.fragment:
        raise ValueError(f"Remote store URI must not contain a query or fragment: {uri}")

    if uri.startswith("abfss://"):
        filesystem, _, host = parsed.netloc.partition("@")
        if not filesystem or not host:
            raise ValueError(f"Invalid Azure Data Lake URI: {uri}")
        account_name = host.removesuffix(".dfs.core.windows.net")
        return RemoteLocation(
            scheme="abfss",
            bucket=filesystem,
            prefix=_validate_remote_path(uri, parsed.path),
            account_name=account_name,
        )

    if parsed.scheme not in REMOTE_STORE_SCHEMES:
        raise ValueError(f"Unsupported remote store URI: {uri}")
    if not parsed.netloc:
        raise ValueError(f"Remote store URI is missing a bucket/container: {uri}")
    return RemoteLocation(
        scheme=parsed.scheme,
        bucket=parsed.netloc,
        prefix=_validate_remote_path(uri, parsed.path),
    )


class RemoteStoreClient:
    def read_text(self, location: RemoteLocation) -> str:
        raise NotImplementedError

    def write_text(self, location: RemoteLocation, content: str, *, content_type: str) -> None:
        raise NotImplementedError


class S3RemoteStoreClient(RemoteStoreClient):
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("s3")
        return self._client

    def read_text(self, location: RemoteLocation) -> str:
        from botocore.exceptions import ClientError

        try:
            response = self.client.get_object(Bucket=location.bucket, Key=location.prefix)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchKey":
                raise FileNotFoundError(location.uri) from exc
            raise
        return response["Body"].read().decode()

    def write_text(self, location: RemoteLocation, content: str, *, content_type: str) -> None:
        self.client.put_object(
            Bucket=location.bucket,
            Key=location.prefix,
            Body=content.encode(),
            ContentType=content_type,
        )


class AzureRemoteStoreClient(RemoteStoreClient):
    def __init__(self, connection_string: str = "", account_name: str = ""):
        self._connection_string = connection_string
        self._account_name = account_name
        self._service_client = None

    @property
    def service_client(self):
        if self._service_client is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            if self._connection_string:
                self._service_client = BlobServiceClient.from_connection_string(self._connection_string)
            else:
                if not self._account_name:
                    raise ValueError(
                        "Azure remote store requires AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT"
                    )
                account_url = f"https://{self._account_name}.blob.core.windows.net"
                self._service_client = BlobServiceClient(
                    account_url=account_url,
                    credential=DefaultAzureCredential(),
                )
        return self._service_client

    def read_text(self, location: RemoteLocation) -> str:
        from azure.core.exceptions import ResourceNotFoundError

        blob_client = self.service_client.get_blob_client(
            container=location.bucket,
            blob=location.prefix,
        )
        try:
            return blob_client.download_blob().readall().decode()
        except ResourceNotFoundError as exc:
            raise FileNotFoundError(location.uri) from exc

    def write_text(self, location: RemoteLocation, content: str, *, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        blob_client = self.service_client.get_blob_client(
            container=location.bucket,
            blob=location.prefix,
        )
        blob_client.upload_blob(
            content.encode(),
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )


class GCSRemoteStoreClient(RemoteStoreClient):
    def __init__(self, credentials_path: str = "", project_id: str = ""):
        self._credentials_path = credentials_path
        self._project_id = project_id
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.cloud import storage

            if self._credentials_path:
                self._client = storage.Client.from_service_account_json(
                    self._credentials_path,
                    project=self._project_id or None,
                )
            else:
                self._client = storage.Client(project=self._project_id or None)
        return self._client

    def read_text(self, location: RemoteLocation) -> str:
        blob = self.client.bucket(location.bucket).blob(location.prefix)
        if not blob.exists():
            raise FileNotFoundError(location.uri)
        return blob.download_as_text()

    def write_text(self, location: RemoteLocation, content: str, *, content_type: str) -> None:
        self.client.bucket(location.bucket).blob(location.prefix).upload_from_string(
            content,
            content_type=content_type,
        )


def build_remote_store_client(config) -> RemoteStoreClient:
    remote_store = getattr(config, "remote_store", None)
    if not remote_store:
        raise ValueError("remote_store is required to build a remote store client")

    location = parse_remote_store(remote_store)
    if location.scheme == "s3":
        return S3RemoteStoreClient()
    if location.scheme in {"az", "azure", "abfss"}:
        account_name = location.account_name or getattr(config, "azure_storage_account", "") or getattr(
            config, "AZURE_STORAGE_ACCOUNT", ""
        )
        connection_string = getattr(config, "azure_storage_connection_string", "") or getattr(
            config, "AZURE_STORAGE_CONNECTION_STRING", ""
        )
        return AzureRemoteStoreClient(
            connection_string=connection_string,
            account_name=account_name,
        )
    if location.scheme == "gs":
        credentials_path = getattr(config, "gcs_credentials_path", "") or getattr(
            config, "GCS_CREDENTIALS_PATH", ""
        )
        project_id = getattr(config, "gcs_project_id", "") or getattr(config, "GCS_PROJECT_ID", "")
        return GCSRemoteStoreClient(
            credentials_path=credentials_path,
            project_id=project_id,
        )
    raise ValueError(f"Unsupported remote store URI: {remote_store}")


class RemoteUploader:
    def __init__(self, remote_store: str):
        self.location = parse_remote_store(remote_store)

    def upload_and_delete(self, local_dir: Path, remote_prefix: str) -> None:
        raise NotImplementedError

    def _iter_files(self, local_dir: Path):
        for file_path in sorted(local_dir.rglob("*")):
            if file_path.is_file():
                yield file_path, file_path.relative_to(local_dir).as_posix()

    def _delete_local_contents(self, local_dir: Path) -> None:
        for path in sorted(local_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()


class S3Uploader(RemoteUploader):
    def __init__(self, remote_store: str):
        super().__init__(remote_store)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("s3")
        return self._client

    def upload_and_delete(self, local_dir: Path, remote_prefix: str) -> None:
        if not local_dir.exists():
            return

        destination = self.location.join(remote_prefix)
        for file_path, relative in self._iter_files(local_dir):
            key = destination.join(relative).prefix
            print(f"    Uploading {relative} -> s3://{destination.bucket}/{key}")
            self.client.upload_file(str(file_path), destination.bucket, key)

        self._delete_local_contents(local_dir)


class AzureUploader(RemoteUploader):
    def __init__(
        self,
        remote_store: str,
        *,
        connection_string: str = "",
        account_name: str = "",
    ):
        super().__init__(remote_store)
        self._connection_string = connection_string
        self._account_name = self.location.account_name or account_name
        self._service_client = None

    @property
    def service_client(self):
        if self._service_client is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            if self._connection_string:
                self._service_client = BlobServiceClient.from_connection_string(self._connection_string)
            else:
                if not self._account_name:
                    raise ValueError(
                        "Azure upload requires AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT"
                    )
                account_url = f"https://{self._account_name}.blob.core.windows.net"
                self._service_client = BlobServiceClient(
                    account_url=account_url,
                    credential=DefaultAzureCredential(),
                )
        return self._service_client

    def upload_and_delete(self, local_dir: Path, remote_prefix: str) -> None:
        if not local_dir.exists():
            return

        destination = self.location.join(remote_prefix)
        for file_path, relative in self._iter_files(local_dir):
            blob_name = destination.join(relative).prefix
            print(f"    Uploading {relative} -> {destination.scheme}://{destination.bucket}/{blob_name}")
            blob_client = self.service_client.get_blob_client(
                container=destination.bucket,
                blob=blob_name,
            )
            with open(file_path, "rb") as fh:
                blob_client.upload_blob(fh, overwrite=True)

        self._delete_local_contents(local_dir)


class GCSUploader(RemoteUploader):
    def __init__(self, remote_store: str, *, credentials_path: str = "", project_id: str = ""):
        super().__init__(remote_store)
        self._credentials_path = credentials_path
        self._project_id = project_id
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.cloud import storage

            if self._credentials_path:
                self._client = storage.Client.from_service_account_json(
                    self._credentials_path,
                    project=self._project_id or None,
                )
            else:
                self._client = storage.Client(project=self._project_id or None)
        return self._client

    def upload_and_delete(self, local_dir: Path, remote_prefix: str) -> None:
        if not local_dir.exists():
            return

        destination = self.location.join(remote_prefix)
        bucket = self.client.bucket(destination.bucket)
        for file_path, relative in self._iter_files(local_dir):
            blob_name = destination.join(relative).prefix
            print(f"    Uploading {relative} -> gs://{destination.bucket}/{blob_name}")
            bucket.blob(blob_name).upload_from_filename(str(file_path))

        self._delete_local_contents(local_dir)


def build_remote_uploader(config) -> RemoteUploader | None:
    remote_store = getattr(config, "remote_store", None)
    if not remote_store:
        return None

    location = parse_remote_store(remote_store)
    if location.scheme == "s3":
        return S3Uploader(remote_store)
    if location.scheme in {"az", "azure", "abfss"}:
        return AzureUploader(
            remote_store,
            connection_string=getattr(config, "azure_storage_connection_string", "")
            or getattr(config, "AZURE_STORAGE_CONNECTION_STRING", ""),
            account_name=location.account_name
            or getattr(config, "azure_storage_account", "")
            or getattr(config, "AZURE_STORAGE_ACCOUNT", ""),
        )
    if location.scheme == "gs":
        return GCSUploader(
            remote_store,
            credentials_path=getattr(config, "gcs_credentials_path", "")
            or getattr(config, "GCS_CREDENTIALS_PATH", ""),
            project_id=getattr(config, "gcs_project_id", "") or getattr(config, "GCS_PROJECT_ID", ""),
        )
    raise ValueError(f"Unsupported remote store URI: {remote_store}")


def default_remote_state_location(remote_store: str) -> RemoteLocation:
    return parse_remote_store(remote_store).join("state.json")


def migrate_legacy_state_prefixes(data: dict) -> dict:
    downloaded = data.get("downloaded", {})
    for years in downloaded.values():
        for codes in years.values():
            for files in codes.values():
                for record in files.values():
                    if "remote_prefix" not in record and "s3_prefix" in record:
                        record["remote_prefix"] = record["s3_prefix"]
    return data
