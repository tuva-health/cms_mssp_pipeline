"""Upload extracted files to S3 and clean up local copies."""

from __future__ import annotations

from pathlib import Path


class S3Uploader:
    def __init__(self, bucket: str):
        self.bucket = bucket
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("s3")
        return self._client

    def upload_and_delete(self, local_dir: Path, s3_prefix: str) -> None:
        """Upload all files in local_dir to S3 under s3_prefix, then delete local copies.

        The directory itself is preserved; only its contents are removed.
        """
        if not local_dir.exists():
            return

        for file_path in sorted(local_dir.rglob("*")):
            if file_path.is_file():
                relative = file_path.relative_to(local_dir)
                key = f"{s3_prefix}/{relative}".replace("\\", "/")
                print(f"    Uploading {relative} → s3://{self.bucket}/{key}")
                self.client.upload_file(str(file_path), self.bucket, key)

        # Delete files then empty directories (deepest first via reverse sort)
        for path in sorted(local_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
