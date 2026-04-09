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
    assert record["remote_prefix"] == "C1234/2025/113"
    assert record["s3_prefix"] == "C1234/2025/113"
    assert "uploaded_at" in record
    assert "downloaded_at" in record


def test_is_uploaded_wrong_last_updated_returns_false(state):
    state.mark_uploaded("C1234", 2025, 113, "file.zip", "2025-01-01T00:00:00.000Z", "C1234/2025/113")
    assert not state.is_uploaded("C1234", 2025, 113, "file.zip", "DIFFERENT_TIMESTAMP")


# ---------------------------------------------------------------------------
# Remote-backed state load / save
# ---------------------------------------------------------------------------

def test_s3_load_on_first_run_returns_empty_state(tmp_path):
    remote_client = MagicMock()
    remote_client.read_text.side_effect = FileNotFoundError("missing")

    with patch("mssp_pipeline.integration.state.build_remote_store_client", return_value=remote_client):
        state = StateManager(tmp_path / "state.json", remote_store="s3://my-bucket")

    assert state.last_run is None
    assert state._data == {"last_run": None, "downloaded": {}}


def test_s3_load_reads_existing_state(tmp_path):
    existing = {
        "last_run": "2026-01-01T00:00:00Z",
        "downloaded": {
            "C1234": {"2025": {"113": {"file.zip": {"last_updated": "2025-01-01", "downloaded_at": "2026-01-01", "uploaded_at": "2026-01-01", "s3_prefix": "C1234/2025/113"}}}}
        },
    }
    remote_client = MagicMock()
    remote_client.read_text.return_value = json.dumps(existing)

    with patch("mssp_pipeline.integration.state.build_remote_store_client", return_value=remote_client):
        state = StateManager(tmp_path / "state.json", remote_store="s3://my-bucket")

    assert state.last_run == "2026-01-01T00:00:00Z"
    assert state.is_uploaded("C1234", 2025, 113, "file.zip", "2025-01-01")
    record = state._data["downloaded"]["C1234"]["2025"]["113"]["file.zip"]
    assert record["remote_prefix"] == "C1234/2025/113"


def test_s3_save_calls_put_object(tmp_path):
    remote_client = MagicMock()
    remote_client.read_text.side_effect = FileNotFoundError("missing")

    with patch("mssp_pipeline.integration.state.build_remote_store_client", return_value=remote_client):
        state = StateManager(tmp_path / "state.json", remote_store="s3://my-bucket/run")
        state.mark_uploaded("C1234", 2025, 113, "file.zip", "2025-01-01", "C1234/2025/113")
        state.save()

    remote_client.write_text.assert_called_once()
    location = remote_client.write_text.call_args.args[0]
    assert location.bucket == "my-bucket"
    assert location.prefix == "run/state.json"
    assert remote_client.write_text.call_args.kwargs["content_type"] == "application/json"
    saved = json.loads(remote_client.write_text.call_args.args[1])
    assert saved["downloaded"]["C1234"]["2025"]["113"]["file.zip"]["remote_prefix"] == "C1234/2025/113"


def test_s3_save_does_not_write_local_file(tmp_path):
    state_file = tmp_path / "state.json"
    remote_client = MagicMock()
    remote_client.read_text.side_effect = FileNotFoundError("missing")

    with patch("mssp_pipeline.integration.state.build_remote_store_client", return_value=remote_client):
        state = StateManager(state_file, remote_store="s3://my-bucket")
        state.save()

    assert not state_file.exists()


def test_azure_load_on_first_run_returns_empty_state(tmp_path):
    remote_client = MagicMock()
    remote_client.read_text.side_effect = FileNotFoundError("missing")

    with patch("mssp_pipeline.integration.state.build_remote_store_client", return_value=remote_client):
        state = StateManager(
            tmp_path / "state.json",
            remote_store="az://container/base",
            azure_storage_connection_string="UseDevelopmentStorage=true",
        )

    assert state._data == {"last_run": None, "downloaded": {}}


def test_azure_save_writes_blob(tmp_path):
    remote_client = MagicMock()
    remote_client.read_text.side_effect = FileNotFoundError("missing")

    with patch("mssp_pipeline.integration.state.build_remote_store_client", return_value=remote_client):
        state = StateManager(
            tmp_path / "state.json",
            remote_store="az://container/base",
            azure_storage_connection_string="UseDevelopmentStorage=true",
        )
        state.mark_uploaded("C1234", 2025, 113, "file.zip", "2025-01-01", "C1234/2025/113")
        state.save()

    location = remote_client.write_text.call_args.args[0]
    assert location.bucket == "container"
    assert location.prefix == "base/state.json"


def test_gcs_load_reads_existing_state(tmp_path):
    remote_client = MagicMock()
    remote_client.read_text.return_value = json.dumps(
        {
            "last_run": "2026-01-01T00:00:00Z",
            "downloaded": {
                "C1234": {"2025": {"113": {"file.zip": {"last_updated": "2025-01-01", "downloaded_at": "2026-01-01", "uploaded_at": "2026-01-01", "remote_prefix": "C1234/2025/113"}}}}
            },
        }
    )

    with patch("mssp_pipeline.integration.state.build_remote_store_client", return_value=remote_client):
        state = StateManager(
            tmp_path / "state.json",
            remote_store="gs://bucket/base",
            gcs_credentials_path="/tmp/creds.json",
            gcs_project_id="project-1",
        )

    assert state.is_uploaded("C1234", 2025, 113, "file.zip", "2025-01-01")


def test_gcs_save_writes_blob(tmp_path):
    remote_client = MagicMock()
    remote_client.read_text.side_effect = FileNotFoundError("missing")

    with patch("mssp_pipeline.integration.state.build_remote_store_client", return_value=remote_client):
        state = StateManager(
            tmp_path / "state.json",
            remote_store="gs://bucket/base",
            gcs_credentials_path="/tmp/creds.json",
            gcs_project_id="project-1",
        )
        state.mark_uploaded("C1234", 2025, 113, "file.zip", "2025-01-01", "C1234/2025/113")
        state.save()

    location = remote_client.write_text.call_args.args[0]
    assert location.bucket == "bucket"
    assert location.prefix == "base/state.json"
