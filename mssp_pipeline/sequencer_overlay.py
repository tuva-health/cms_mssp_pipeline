"""Run the sequencer engine with a plan-provider overlay fetched from S3.

The engine (``mssp_pipeline.sequencer``) bakes in no plan: a client overlay
supplies the ``module:callable`` plan provider. On an operator's machine that
overlay is a git-ignored directory placed on ``PYTHONPATH``. Inside the
pipeline image it does not exist -- the image is built from the canonical tree
alone -- so a scheduled driver task materializes it at start from an S3 prefix
the deploy step published, then hands over to :func:`sequence_main`.

Contract
--------

Inputs arrive from the task's environment and command (the EventBridge target
input); nothing client-specific lives here.

``MSSP_PLAN_OVERLAY_URI`` (or ``--overlay-uri``)
    ``s3://<bucket>/<prefix>`` holding the overlay. Required; a bucket root is
    rejected.
``MSSP_PLAN_OVERLAY_DIR`` (or ``--overlay-dir``)
    Local directory to materialize into. Default ``/tmp/mssp-plan-overlay``.
    It is emptied before the fetch so nothing from an earlier run survives.
``--plan-provider``
    ``module:callable`` resolved from the fetched overlay, passed through to
    ``mssp-sequence`` unchanged.

The layout under the prefix mirrors a client overlay directory, so a plan
module written for the on-disk overlay runs unchanged from the fetched copy
(it resolves ``rendered/`` as a sibling of its own ``sequencer/`` directory,
i.e. ``Path(__file__).resolve().parent.parent / "rendered"``)::

    <prefix>/sequencer/<plan_module>.py
    <prefix>/rendered/task-definition-arns.json

Every object under the prefix is copied to the overlay directory, preserving
layout, and ``<dir>/sequencer`` is put at the *front* of ``sys.path`` so the
fetched plan module wins over anything installed with the same name -- name
plan modules distinctively (never after a stdlib or site-packages module).

Fail closed: no URI, a bucket-root prefix, an empty prefix, a fetched overlay
with no ``sequencer/`` directory, or a key that would land outside the
destination directory aborts before the engine runs.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Sequence

from mssp_pipeline.sequencer import sequence_main

DEFAULT_OVERLAY_DIR = "/tmp/mssp-plan-overlay"
PLAN_SUBDIR = "sequencer"
OVERLAY_URI_ENV = "MSSP_PLAN_OVERLAY_URI"
OVERLAY_DIR_ENV = "MSSP_PLAN_OVERLAY_DIR"


class OverlayError(ValueError):
    """The overlay location or its contents are unusable."""


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split ``s3://bucket/prefix`` into ``(bucket, prefix)``.

    A prefix is required: copying a whole bucket root into the task is never
    the intent, and an unscoped listing would be a policy mistake, not a plan.
    """
    if not uri.startswith("s3://"):
        raise OverlayError(f"overlay URI must start with s3://, got {uri!r}")
    bucket, _, prefix = uri[len("s3://"):].partition("/")
    prefix = prefix.strip("/")
    if not bucket or not prefix:
        raise OverlayError(f"overlay URI must be s3://<bucket>/<prefix>, got {uri!r}")
    return bucket, prefix


def _relative_key(key: str, prefix: str) -> PurePosixPath | None:
    """The path of ``key`` below ``prefix``; ``None`` for the prefix marker or a
    "directory" key. Rejects anything that could escape the destination."""
    if not key.startswith(prefix + "/"):
        return None
    rel = key[len(prefix) + 1:]
    if not rel or rel.endswith("/"):
        return None
    path = PurePosixPath(rel)
    if path.is_absolute() or ".." in path.parts:
        raise OverlayError(f"refusing overlay key that escapes its prefix: {key!r}")
    return path


def _empty_directory(root: Path) -> None:
    """Start from a clean directory so a stale module or a removed ``rendered/``
    file from an earlier materialization can never be imported by mistake."""
    if root == Path(root.anchor) or root == Path.home():
        raise OverlayError(f"refusing to use {root} as the overlay directory")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)


def fetch_overlay(uri: str, dest: str | os.PathLike, *, s3=None) -> list[Path]:
    """Copy every object under ``uri`` into ``dest``, preserving layout.

    ``dest`` is emptied first. ``s3`` is a boto3 S3 client (injected by tests);
    by default one is built from the task's ambient AWS session (the task
    role). Returns the files written, in key order. An empty prefix is an
    error: a driver with no plan must not start.
    """
    bucket, prefix = parse_s3_uri(uri)
    if s3 is None:  # pragma: no cover - live only
        import boto3

        s3 = boto3.client("s3")

    root = Path(dest).resolve()
    _empty_directory(root)
    written: list[Path] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix + "/"):
        for obj in page.get("Contents", []):
            rel = _relative_key(obj["Key"], prefix)
            if rel is None:
                continue
            target = root / Path(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(target))
            written.append(target)
    if not written:
        raise OverlayError(f"no overlay objects found under {uri}")
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mssp_pipeline.sequencer_overlay",
        description=(
            "Materialize a plan-provider overlay from S3, put it on sys.path, "
            "then run mssp-sequence with the given plan provider."
        ),
    )
    parser.add_argument(
        "--plan-provider",
        required=True,
        help="'module:callable' returning a SequencerJob, resolved from the fetched overlay.",
    )
    parser.add_argument(
        "--overlay-uri",
        default=os.environ.get(OVERLAY_URI_ENV, ""),
        help=f"s3://bucket/prefix holding the overlay (default: ${OVERLAY_URI_ENV}).",
    )
    parser.add_argument(
        "--overlay-dir",
        default=os.environ.get(OVERLAY_DIR_ENV, DEFAULT_OVERLAY_DIR),
        help=f"Local directory to materialize into; emptied first (default: ${OVERLAY_DIR_ENV} or {DEFAULT_OVERLAY_DIR}).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.overlay_uri:
        parser.error(f"--overlay-uri or ${OVERLAY_URI_ENV} is required")
    try:
        files = fetch_overlay(args.overlay_uri, args.overlay_dir)
    except OverlayError as exc:
        parser.error(str(exc))
    plan_dir = Path(args.overlay_dir).resolve() / PLAN_SUBDIR
    if not plan_dir.is_dir():
        parser.error(f"overlay has no {PLAN_SUBDIR!r} directory (fetched: {[str(f) for f in files]})")

    print(f"[sequencer-overlay] materialized {len(files)} file(s) from {args.overlay_uri} into {args.overlay_dir}")
    sys.path.insert(0, str(plan_dir))
    return sequence_main(["--plan-provider", args.plan_provider])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
