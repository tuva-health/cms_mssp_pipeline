"""Tests for state.py."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mssp_pipeline.integration.state import StateManager


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "state.json"


@pytest.fixture
def state(state_file):
    return StateManager(state_file)


def test_initial_state_is_empty(state):
    assert state.last_run is None
    assert not state.is_downloaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z")


def test_mark_and_check_downloaded(state):
    state.mark_downloaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z")
    assert state.is_downloaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z")


def test_different_last_updated_not_considered_downloaded(state):
    state.mark_downloaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z")
    assert not state.is_downloaded("C1234", 2025, 113, "file.zip", "2025-06-01T00:00:00.000Z")


def test_different_filename_not_considered_downloaded(state):
    state.mark_downloaded("C1234", 2025, 113, "file_a.zip", "2025-01-01T00:00:00.000Z")
    assert not state.is_downloaded("C1234", 2025, 113, "file_b.zip", "2025-01-01T00:00:00.000Z")


def test_save_persists_to_disk(state, state_file):
    state.mark_downloaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z")
    state.save()
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert "last_run" in data
    assert data["downloaded"]["C1234"]["2025"]["113"]["file.zip"]["last_updated"] == "2025-01-01T00:00:00.000Z"


def test_load_from_existing_file(state_file):
    data = {
        "last_run": "2026-01-01T00:00:00Z",
        "downloaded": {
            "C1234": {
                "2025": {
                    "113": {
                        "file.zip": {
                            "last_updated": "2025-01-01T00:00:00.000Z",
                            "downloaded_at": "2026-01-01T00:00:00Z",
                        }
                    }
                }
            }
        },
    }
    state_file.write_text(json.dumps(data))
    state = StateManager(state_file)
    assert state.last_run == "2026-01-01T00:00:00Z"
    assert state.is_downloaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z")


def test_reset_clears_state(state):
    state.mark_downloaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z")
    state.reset()
    assert not state.is_downloaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z")
    assert state.last_run is None


def test_multiple_aco_year_code_combinations(state):
    state.mark_downloaded("C1234", 2025, 113, "a.zip", "2025-01-01T00:00:00.000Z")
    state.mark_downloaded("C1234", 2024, 116, "b.zip", "2024-06-01T00:00:00.000Z")
    state.mark_downloaded("C9999", 2025, 113, "c.zip", "2025-03-01T00:00:00.000Z")

    assert state.is_downloaded("C1234", 2025, 113, "a.zip", "2025-01-01T00:00:00.000Z")
    assert state.is_downloaded("C1234", 2024, 116, "b.zip", "2024-06-01T00:00:00.000Z")
    assert state.is_downloaded("C9999", 2025, 113, "c.zip", "2025-03-01T00:00:00.000Z")
    assert not state.is_downloaded("C1234", 2025, 113, "b.zip", "2025-01-01T00:00:00.000Z")


# ---------------------------------------------------------------------------
# is_uploaded / mark_uploaded
# ---------------------------------------------------------------------------

def test_downloaded_without_upload_is_not_uploaded(state):
    state.mark_downloaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z")
    assert not state.is_uploaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z")


def test_mark_and_check_uploaded(state):
    state.mark_uploaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z", "C1234/2025/113")
    assert state.is_uploaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z")
    # is_downloaded still returns True for an uploaded file
    assert state.is_downloaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z")


def test_mark_uploaded_stores_s3_prefix(state):
    state.mark_uploaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z", "C1234/2025/113")
    record = state._data["downloaded"]["C1234"]["2025"]["113"]["file.zip"]
    assert record["s3_prefix"] == "C1234/2025/113"
    assert "uploaded_at" in record
    assert "downloaded_at" in record


def test_is_uploaded_wrong_last_updated_returns_false(state):
    state.mark_uploaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z", "C1234/2025/113")
    assert not state.is_uploaded("C1234", 2025, 113, "file.zip", "DIFFERENT_TIMESTAMP")


# ---------------------------------------------------------------------------
# S3-backed state load / save
# ---------------------------------------------------------------------------

def test_s3_load_on_first_run_returns_empty_state(tmp_path):
    from botocore.exceptions import ClientError

    mock_client = MagicMock()
    mock_client.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "GetObject"
    )

    with patch("boto3.client", return_value=mock_client):
        state = StateManager(tmp_path / "state.json", s3_bucket="my-bucket")

    assert state.last_run is None
    assert state._data == {"last_run": None, "downloaded": {}}


def test_s3_load_reads_existing_state(tmp_path):
    existing = {
        "last_run": "2026-01-01T00:00:00Z",
        "downloaded": {
            "C1234": {"2025": {"113": {"file.zip": {"last_updated": "2025-01-01", "downloaded_at": "2026-01-01", "uploaded_at": "2026-01-01", "s3_prefix": "C1234/2025/113"}}}}
        },
    }
    mock_client = MagicMock()
    mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(existing).encode())}

    with patch("boto3.client", return_value=mock_client):
        state = StateManager(tmp_path / "state.json", s3_bucket="my-bucket")

    assert state.last_run == "2026-01-01T00:00:00Z"
    assert state.is_uploaded("C1234", 2025, 113, "file.zip", "2025-01-01")


def test_s3_save_calls_put_object(tmp_path):
    from botocore.exceptions import ClientError

    mock_client = MagicMock()
    mock_client.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "GetObject"
    )

    with patch("boto3.client", return_value=mock_client):
        state = StateManager(tmp_path / "state.json", s3_bucket="my-bucket", s3_state_key="run/state.json")
        state.mark_uploaded("C1234", 2025, 113, "file.zip", "2025-01-01", "C1234/2025/113")
        state.save()

    mock_client.put_object.assert_called_once()
    call_kwargs = mock_client.put_object.call_args[1]
    assert call_kwargs["Bucket"] == "my-bucket"
    assert call_kwargs["Key"] == "run/state.json"
    assert call_kwargs["ContentType"] == "application/json"
    saved = json.loads(call_kwargs["Body"].decode())
    assert saved["downloaded"]["C1234"]["2025"]["113"]["file.zip"]["s3_prefix"] == "C1234/2025/113"


def test_s3_save_does_not_write_local_file(tmp_path):
    from botocore.exceptions import ClientError

    mock_client = MagicMock()
    mock_client.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "Not found"}}, "GetObject"
    )

    state_file = tmp_path / "state.json"

    with patch("boto3.client", return_value=mock_client):
        state = StateManager(state_file, s3_bucket="my-bucket")
        state.save()

    assert not state_file.exists()
