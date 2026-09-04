"""``mssp_pipeline.sequencer_overlay``: fetch a plan-provider overlay from S3, then run the engine.

A scheduled driver task runs the canonical image, which carries no client plan
module. The shim materializes the overlay from an S3 prefix at start (layout
preserved, so the plan module finds its sibling ``rendered/`` files), prepends
``<dir>/sequencer`` to ``sys.path`` and hands over to ``sequence_main``. Fail
closed on a missing URI, a bucket-root or empty prefix, an overlay with no
``sequencer/`` directory, and any key that would escape the destination. The
destination is emptied first so nothing from an earlier run is importable.
Exercised against moto; synthetic values only.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from mssp_pipeline import sequencer_overlay as overlay

BUCKET = "example-overlay-bucket"
PREFIX = "sequencer-overlay/example"
URI = f"s3://{BUCKET}/{PREFIX}"
PLAN_MODULE = "example_sequence_plan"
PLAN_SOURCE = "def job():\n    return 'example-job'\n"
ARNS = {"mssp-pipeline-download": "arn:aws:ecs:us-east-1:111122223333:task-definition/mssp-pipeline-download:3"}


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with moto.mock_aws():
        yield boto3.client("s3", region_name="us-east-1")


def _seed(s3, *, with_overlay: bool = True) -> None:
    s3.create_bucket(Bucket=BUCKET)
    if with_overlay:
        s3.put_object(Bucket=BUCKET, Key=f"{PREFIX}/", Body=b"")  # "directory" marker
        s3.put_object(Bucket=BUCKET, Key=f"{PREFIX}/sequencer/{PLAN_MODULE}.py", Body=PLAN_SOURCE.encode())
        s3.put_object(
            Bucket=BUCKET,
            Key=f"{PREFIX}/rendered/task-definition-arns.json",
            Body=json.dumps(ARNS).encode(),
        )
    # A sibling prefix that must never be copied.
    s3.put_object(Bucket=BUCKET, Key="sequencer-overlay/other/sequencer/other_plan.py", Body=b"# no\n")
    s3.put_object(Bucket=BUCKET, Key=f"{PREFIX}-not-mine/x.py", Body=b"# no\n")


def test_fetch_copies_the_prefix_preserving_layout(aws, tmp_path) -> None:
    _seed(aws)
    dest = tmp_path / "ovl"

    written = overlay.fetch_overlay(URI, dest)

    assert sorted(p.relative_to(dest).as_posix() for p in written) == [
        "rendered/task-definition-arns.json",
        f"sequencer/{PLAN_MODULE}.py",
    ]
    assert (dest / "sequencer" / f"{PLAN_MODULE}.py").read_text() == PLAN_SOURCE
    assert json.loads((dest / "rendered" / "task-definition-arns.json").read_text()) == ARNS
    assert not list(dest.rglob("other_plan.py")) and not list(dest.rglob("x.py"))


class _EscapingS3:
    """A listing whose key would land outside the destination directory."""

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, **kwargs):
        yield {"Contents": [{"Key": f"{PREFIX}/../escape.py"}]}

    def download_file(self, *args):  # pragma: no cover - must never be reached
        raise AssertionError("download attempted for an escaping key")


def test_fetch_empties_the_destination_first(aws, tmp_path) -> None:
    _seed(aws)
    dest = tmp_path / "ovl"
    stale = dest / "sequencer" / "stale_plan.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("# from an earlier run\n")
    (dest / "rendered").mkdir()
    (dest / "rendered" / "removed.json").write_text("{}")

    overlay.fetch_overlay(URI, dest)

    assert not stale.exists() and not (dest / "rendered" / "removed.json").exists()
    assert (dest / "sequencer" / f"{PLAN_MODULE}.py").exists()


def test_fetch_refuses_the_filesystem_root_as_destination() -> None:
    with pytest.raises(overlay.OverlayError, match="refusing to use"):
        overlay.fetch_overlay(URI, "/", s3=_EscapingS3())


@pytest.mark.parametrize("uri", ["s3://bucket-only", "s3://bucket-only/", "https://example/prefix", "bucket/prefix"])
def test_uri_must_name_a_bucket_and_a_prefix(uri: str) -> None:
    with pytest.raises(overlay.OverlayError):
        overlay.parse_s3_uri(uri)


def test_fetch_fails_closed_when_the_prefix_is_empty(aws, tmp_path) -> None:
    _seed(aws, with_overlay=False)
    with pytest.raises(overlay.OverlayError, match="no overlay objects"):
        overlay.fetch_overlay(URI, tmp_path / "ovl")


def test_fetch_rejects_keys_that_escape_the_destination(tmp_path) -> None:
    dest = tmp_path / "ovl"
    with pytest.raises(overlay.OverlayError, match="escapes"):
        overlay.fetch_overlay(URI, dest, s3=_EscapingS3())
    assert not (tmp_path / "escape.py").exists()


def test_main_materializes_then_hands_over_to_the_engine(aws, tmp_path, monkeypatch) -> None:
    _seed(aws)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv(overlay.OVERLAY_URI_ENV, URI)
    handed: dict[str, list[str]] = {}

    def fake_sequence_main(argv):
        handed["argv"] = list(argv)
        return 0

    monkeypatch.setattr(overlay, "sequence_main", fake_sequence_main)
    dest = tmp_path / "ovl"

    rc = overlay.main(["--plan-provider", f"{PLAN_MODULE}:job", "--overlay-dir", str(dest)])

    assert rc == 0
    assert handed["argv"] == ["--plan-provider", f"{PLAN_MODULE}:job"]
    assert Path(sys.path[0]) == (dest / "sequencer").resolve()
    # The fetched module is what the engine's provider loader will import.
    sys.modules.pop(PLAN_MODULE, None)
    assert importlib.import_module(PLAN_MODULE).job() == "example-job"
    sys.modules.pop(PLAN_MODULE, None)


def test_main_fails_closed_when_the_overlay_has_no_sequencer_directory(aws, tmp_path, monkeypatch) -> None:
    aws.create_bucket(Bucket=BUCKET)
    aws.put_object(Bucket=BUCKET, Key=f"{PREFIX}/rendered/task-definition-arns.json", Body=json.dumps(ARNS).encode())
    monkeypatch.setenv(overlay.OVERLAY_URI_ENV, URI)
    monkeypatch.setattr(overlay, "sequence_main", lambda argv: pytest.fail("engine must not start"))

    with pytest.raises(SystemExit) as exc:
        overlay.main(["--plan-provider", f"{PLAN_MODULE}:job", "--overlay-dir", str(tmp_path / "ovl")])
    assert exc.value.code == 2


def test_main_without_an_overlay_uri_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(overlay.OVERLAY_URI_ENV, raising=False)
    with pytest.raises(SystemExit) as exc:
        overlay.main(["--plan-provider", "m:c", "--overlay-dir", str(tmp_path)])
    assert exc.value.code == 2


def test_main_requires_a_plan_provider(monkeypatch) -> None:
    monkeypatch.setenv(overlay.OVERLAY_URI_ENV, URI)
    with pytest.raises(SystemExit) as exc:
        overlay.main([])
    assert exc.value.code == 2
