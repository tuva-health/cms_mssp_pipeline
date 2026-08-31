#!/usr/bin/env python3
"""Verify pipeline release-provenance metadata against the source checkout.

This is the client-neutral release-provenance check for the generic image. It
proves that a ``release-metadata/<id>.json`` file describes an *immutable*,
reproducible build of the current checkout:

* the field set is exactly the release contract (no more, no less);
* ``image`` is pinned by ``@sha256:`` digest, never a mutable tag;
* ``source_commit`` is a full commit id and (when ``--repo`` is given) matches
  the checkout's ``HEAD``;
* ``dependency_checksum`` matches the sha256 of ``uv.lock`` in the checkout;
* ``release_id`` is non-empty.

No client identity (registry, account, destination, backend) is encoded here --
those live in the private build/release policy that produces the metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# repository@sha256:<64 lowercase hex> -- an immutable image reference.
IMMUTABLE_IMAGE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")

REQUIRED_FIELDS = {"image", "source_commit", "release_id", "dependency_checksum"}


def read_json(path: Path, label: str, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"cannot read {label}: {error}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label} must contain a JSON object")
        return {}
    return value


def file_sha256(path: Path, label: str, errors: list[str]) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        errors.append(f"cannot read {label}: {error}")
        return ""


def git_head(repo: Path, errors: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        errors.append(f"cannot resolve checkout commit: {error}")
        return ""
    return result.stdout.strip()


def verify_release_metadata(
    metadata: dict[str, Any], repo: Path | None, errors: list[str]
) -> None:
    if set(metadata) != REQUIRED_FIELDS:
        errors.append(
            "release metadata fields do not match the contract "
            f"(expected {sorted(REQUIRED_FIELDS)}, got {sorted(metadata)})"
        )
        return

    image = str(metadata.get("image", ""))
    if not IMMUTABLE_IMAGE.fullmatch(image):
        errors.append("release image is not an immutable repository@sha256 digest")

    source_commit = str(metadata.get("source_commit", ""))
    if not FULL_COMMIT.fullmatch(source_commit):
        errors.append("release source_commit must be a full 40-character commit id")

    if not str(metadata.get("release_id", "")).strip():
        errors.append("release_id must not be empty")

    dependency_checksum = str(metadata.get("dependency_checksum", ""))
    if not SHA256.fullmatch(dependency_checksum):
        errors.append("dependency_checksum is not a sha256 digest")

    if repo is not None:
        head = git_head(repo, errors)
        if head and source_commit != head:
            errors.append("release source_commit does not match checkout HEAD")
        lock_checksum = file_sha256(repo / "uv.lock", "uv.lock", errors)
        if lock_checksum and dependency_checksum != lock_checksum:
            errors.append("dependency_checksum does not match uv.lock")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata", type=Path, help="release-metadata JSON file")
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="checkout to cross-check source_commit and uv.lock against",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    errors: list[str] = []
    metadata = read_json(args.metadata, "release metadata", errors)
    if not errors:
        verify_release_metadata(metadata, args.repo, errors)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("release provenance verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
