"""Persistent state tracking for downloaded files."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class StateManager:
    def __init__(
        self,
        state_file: Path,
        s3_bucket: str | None = None,
        s3_state_key: str = "state.json",
    ):
        self._path = state_file
        self._s3_bucket = s3_bucket
        self._s3_state_key = s3_state_key
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self._s3_bucket:
            return self._load_from_s3()
        if self._path.exists():
            with open(self._path) as f:
                return json.load(f)
        return {"last_run": None, "downloaded": {}}

    def _load_from_s3(self) -> dict:
        import boto3
        from botocore.exceptions import ClientError

        client = boto3.client("s3")
        try:
            response = client.get_object(Bucket=self._s3_bucket, Key=self._s3_state_key)
            return json.loads(response["Body"].read())
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return {"last_run": None, "downloaded": {}}
            raise

    def save(self) -> None:
        self._data["last_run"] = datetime.now(timezone.utc).isoformat()
        content = json.dumps(self._data, indent=2)
        if self._s3_bucket:
            import boto3

            boto3.client("s3").put_object(
                Bucket=self._s3_bucket,
                Key=self._s3_state_key,
                Body=content.encode(),
                ContentType="application/json",
            )
        else:
            with open(self._path, "w") as f:
                f.write(content)

    def reset(self) -> None:
        self._data = {"last_run": None, "downloaded": {}}

    def is_downloaded(self, aco: str, year: int, code: int, filename: str, last_updated: str) -> bool:
        """Return True if this exact file (by filename + last_updated) is already in state."""
        key = str(code)
        try:
            record = self._data["downloaded"][aco][str(year)][key][filename]
            return record["last_updated"] == last_updated
        except KeyError:
            return False

    def is_uploaded(self, aco: str, year: int, code: int, filename: str, last_updated: str) -> bool:
        """Return True if this file was successfully uploaded to S3."""
        key = str(code)
        try:
            record = self._data["downloaded"][aco][str(year)][key][filename]
            return record["last_updated"] == last_updated and "uploaded_at" in record
        except KeyError:
            return False

    def mark_downloaded(self, aco: str, year: int, code: int, filename: str, last_updated: str) -> None:
        d = self._data["downloaded"]
        d.setdefault(aco, {}).setdefault(str(year), {}).setdefault(str(code), {})[filename] = {
            "last_updated": last_updated,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
        }

    def mark_uploaded(
        self,
        aco: str,
        year: int,
        code: int,
        filename: str,
        last_updated: str,
        s3_prefix: str,
    ) -> None:
        """Record a successful S3 upload. Sets both downloaded_at and uploaded_at."""
        self.mark_downloaded(aco, year, code, filename, last_updated)
        record = self._data["downloaded"][aco][str(year)][str(code)][filename]
        record["s3_prefix"] = s3_prefix
        record["uploaded_at"] = datetime.now(timezone.utc).isoformat()

    @property
    def last_run(self) -> str | None:
        return self._data.get("last_run")
