"""Integration-style tests for downloader.py with all subprocess calls mocked."""

import shutil
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from mssp_pipeline.integration.config import Config
from mssp_pipeline.integration.downloader import Downloader
from mssp_pipeline.integration.state import StateManager


# ---------------------------------------------------------------------------
# Minimal --list and --view output used across tests
# ---------------------------------------------------------------------------

LIST_ONE_CODE = """
-----Assignment Report, Code 116

Session closed.
"""

VIEW_TWO_FILES = """
 1 of 2 - P.C1234.ACO.ZCY25.D250122.T1621240.zip (10 MB) Last Updated: 2025-01-24T14:33:13.000Z
 2 of 2 - P.C1234.ACO.ZCY25.D250212.T1202370.zip (5 MB) Last Updated: 2025-02-12T22:13:12.000Z

Session closed.
"""

VIEW_ONE_FILE = """
 1 of 1 - P.C1234.ACO.ZCY25.D250122.T1621240.zip (10 MB) Last Updated: 2025-01-24T14:33:13.000Z

Session closed.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(tmp_path, remote_store=None) -> Config:
    from datetime import date
    return Config(
        aco="C1234",
        start_year=date.today().year,  # only current year, so exactly one iteration
        output_dir=tmp_path / "downloads",
        state_file=tmp_path / "state.json",
        cli_path=Path("./acoms-cli"),
        staging_dir=tmp_path / "staging",  # isolated staging area for tests
        remote_store=remote_store,
        azure_storage_account="acct",
        gcs_project_id="project-1",
    )


def make_fake_zip(directory: Path, name: str) -> Path:
    """Create a real (empty) zip file so zipfile.ZipFile can open it."""
    zip_path = directory / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("dummy.txt", "content")
    return zip_path


def make_zip_with_member(directory: Path, name: str, member_name: str, content: str = "content") -> Path:
    zip_path = directory / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(member_name, content)
    return zip_path


# ---------------------------------------------------------------------------
# Non-S3 tests (existing behaviour)
# ---------------------------------------------------------------------------

@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
def test_downloads_new_files(mock_list, mock_view, mock_download, tmp_path):
    cfg = make_config(tmp_path)
    state = StateManager(cfg.state_file)

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = VIEW_TWO_FILES

    def fake_download(cli_path, aco, year, code, created_after=None):
        # Simulate the CLI writing zip files into staging_dir
        cfg.staging_dir.mkdir(parents=True, exist_ok=True)
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.ZCY25.D250122.T1621240.zip")
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.ZCY25.D250212.T1202370.zip")

    mock_download.side_effect = fake_download

    downloader = Downloader(cfg, state)
    downloader.run()

    # Both files should now be in state
    assert state.is_downloaded("C1234", cfg.current_year, 116, "P.C1234.ACO.ZCY25.D250122.T1621240.zip", "2025-01-24T14:33:13.000Z")
    assert state.is_downloaded("C1234", cfg.current_year, 116, "P.C1234.ACO.ZCY25.D250212.T1202370.zip", "2025-02-12T22:13:12.000Z")

    # Zip files should be deleted; contents extracted into per-zip subdirectories
    out_dir = cfg.output_dir / "C1234" / str(cfg.current_year) / "116"
    assert not list(out_dir.glob("*.zip"))
    assert (out_dir / "P.C1234.ACO.ZCY25.D250122.T1621240" / "dummy.txt").exists()
    assert (out_dir / "P.C1234.ACO.ZCY25.D250212.T1202370" / "dummy.txt").exists()


@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
def test_skips_already_downloaded_files(mock_list, mock_view, mock_download, tmp_path):
    cfg = make_config(tmp_path)
    state = StateManager(cfg.state_file)

    # Pre-populate state with both files for the current year
    state.mark_downloaded("C1234", cfg.current_year, 116, "P.C1234.ACO.ZCY25.D250122.T1621240.zip", "2025-01-24T14:33:13.000Z")
    state.mark_downloaded("C1234", cfg.current_year, 116, "P.C1234.ACO.ZCY25.D250212.T1202370.zip", "2025-02-12T22:13:12.000Z")

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = VIEW_TWO_FILES

    downloader = Downloader(cfg, state)
    downloader.run()

    mock_download.assert_not_called()


@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
def test_redownloads_updated_file(mock_list, mock_view, mock_download, tmp_path):
    cfg = make_config(tmp_path)
    state = StateManager(cfg.state_file)

    # File 1 is already downloaded; file 2 has a DIFFERENT last_updated → needs re-download
    state.mark_downloaded("C1234", cfg.current_year, 116, "P.C1234.ACO.ZCY25.D250122.T1621240.zip", "2025-01-24T14:33:13.000Z")
    state.mark_downloaded("C1234", cfg.current_year, 116, "P.C1234.ACO.ZCY25.D250212.T1202370.zip", "OLD_TIMESTAMP")

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = VIEW_TWO_FILES  # file 2 now has 2025-02-12T22:13:12.000Z

    def fake_download(cli_path, aco, year, code, created_after=None):
        cfg.staging_dir.mkdir(parents=True, exist_ok=True)
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.ZCY25.D250212.T1202370.zip")

    mock_download.side_effect = fake_download

    downloader = Downloader(cfg, state)
    downloader.run()

    mock_download.assert_called_once()
    # The createdAfter should be the creation date of file 2 (2025-02-12)
    assert mock_download.call_args[1].get("created_after") == "2025-02-12"


@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
def test_uses_oldest_new_file_date_for_created_after(mock_list, mock_view, mock_download, tmp_path):
    cfg = make_config(tmp_path)
    state = StateManager(cfg.state_file)

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = VIEW_TWO_FILES  # files: D250122 and D250212

    def fake_download(cli_path, aco, year, code, created_after=None):
        cfg.staging_dir.mkdir(parents=True, exist_ok=True)
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.ZCY25.D250122.T1621240.zip")
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.ZCY25.D250212.T1202370.zip")

    mock_download.side_effect = fake_download

    downloader = Downloader(cfg, state)
    downloader.run()

    # createdAfter should be the date of the OLDER file (D250122 = 2025-01-22)
    assert mock_download.call_args[1].get("created_after") == "2025-01-22"


@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
def test_no_files_from_view_skips_download(mock_list, mock_view, mock_download, tmp_path):
    cfg = make_config(tmp_path)
    state = StateManager(cfg.state_file)

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = "Session closed."  # no file entries

    downloader = Downloader(cfg, state)
    downloader.run()

    mock_download.assert_not_called()


@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
def test_sentinel_date_d259999_falls_back_to_no_filter(mock_list, mock_view, mock_download, tmp_path):
    """CMS uses D259999 as a placeholder date (month=99); must not crash."""
    cfg = make_config(tmp_path)
    state = StateManager(cfg.state_file)

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = """
 1 of 3 - P.C1234.ACO.QQR.D259999.T0100000.zip (253 KB) Last Updated: 2025-08-12T16:02:48.000Z
 2 of 3 - P.C1234.ACO.QQR.D259999.T0200000.zip (241 KB) Last Updated: 2025-09-30T16:25:15.000Z
 3 of 3 - P.C1234.ACO.QQR.D259999.T0300000.zip (635 KB) Last Updated: 2025-12-18T18:24:21.000Z

Session closed.
"""

    def fake_download(cli_path, aco, year, code, created_after=None):
        cfg.staging_dir.mkdir(parents=True, exist_ok=True)
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.QQR.D259999.T0100000.zip")
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.QQR.D259999.T0200000.zip")
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.QQR.D259999.T0300000.zip")

    mock_download.side_effect = fake_download

    downloader = Downloader(cfg, state)
    downloader.run()  # must not raise

    # Download was called without a createdAfter filter (date unparseable)
    mock_download.assert_called_once()
    assert mock_download.call_args[1].get("created_after") is None


@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
def test_downloads_non_zip_files(mock_list, mock_view, mock_download, tmp_path):
    """Code 183 (XREF) delivers .txt files; they should be moved but not extracted."""
    cfg = make_config(tmp_path)
    state = StateManager(cfg.state_file)

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = """
 1 of 1 - P.C1234.ACO.MBIY25.D250103.T1033540.txt (3.82 KB) Last Updated: 2025-01-07T20:38:22.000Z

Session closed.
"""

    def fake_download(cli_path, aco, year, code, created_after=None):
        cfg.staging_dir.mkdir(parents=True, exist_ok=True)
        (cfg.staging_dir / "P.C1234.ACO.MBIY25.D250103.T1033540.txt").write_text("data")

    mock_download.side_effect = fake_download

    downloader = Downloader(cfg, state)
    downloader.run()

    out_dir = cfg.output_dir / "C1234" / str(cfg.current_year) / "116"
    assert (out_dir / "P.C1234.ACO.MBIY25.D250103.T1033540.txt").exists()
    assert state.is_downloaded("C1234", cfg.current_year, 116, "P.C1234.ACO.MBIY25.D250103.T1033540.txt", "2025-01-07T20:38:22.000Z")


@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
def test_rejects_zip_traversal_entries_without_advancing_state(mock_list, mock_view, mock_download, tmp_path):
    cfg = make_config(tmp_path)
    state = StateManager(cfg.state_file)

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = VIEW_ONE_FILE

    def fake_download(cli_path, aco, year, code, created_after=None):
        cfg.staging_dir.mkdir(parents=True, exist_ok=True)
        make_zip_with_member(
            cfg.staging_dir,
            "P.C1234.ACO.ZCY25.D250122.T1621240.zip",
            "../evil.txt",
            "owned",
        )

    mock_download.side_effect = fake_download

    downloader = Downloader(cfg, state)

    with pytest.raises(ValueError, match="Unsafe archive member"):
        downloader.run()

    out_dir = cfg.output_dir / "C1234" / str(cfg.current_year) / "116"
    assert not (out_dir.parent / "evil.txt").exists()
    assert not state.is_downloaded(
        "C1234",
        cfg.current_year,
        116,
        "P.C1234.ACO.ZCY25.D250122.T1621240.zip",
        "2025-01-24T14:33:13.000Z",
    )


def test_rejects_absolute_zip_member_paths(tmp_path):
    cfg = make_config(tmp_path)
    state = StateManager(cfg.state_file)
    downloader = Downloader(cfg, state)

    out_dir = cfg.output_dir / cfg.aco / str(cfg.current_year) / "116"
    out_dir.mkdir(parents=True, exist_ok=True)
    make_zip_with_member(out_dir, "absolute.zip", "/tmp/evil.txt", "owned")

    with pytest.raises(ValueError, match="Unsafe archive member"):
        downloader._extract_and_cleanup(out_dir)

    assert not (tmp_path / "evil.txt").exists()


# ---------------------------------------------------------------------------
# Remote upload mode tests
# ---------------------------------------------------------------------------

@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
@patch("mssp_pipeline.integration.downloader.build_remote_uploader")
def test_s3_uploads_and_deletes_local_files(mock_uploader_factory, mock_list, mock_view, mock_download, tmp_path):
    cfg = make_config(tmp_path, remote_store="s3://my-bucket")
    state = StateManager(cfg.state_file)

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = VIEW_TWO_FILES

    mock_uploader = MagicMock()
    mock_uploader_factory.return_value = mock_uploader

    def fake_download(cli_path, aco, year, code, created_after=None):
        cfg.staging_dir.mkdir(parents=True, exist_ok=True)
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.ZCY25.D250122.T1621240.zip")
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.ZCY25.D250212.T1202370.zip")

    def fake_upload_and_delete(local_dir, s3_prefix):
        # Simulate what S3Uploader actually does: remove local contents
        for path in sorted(local_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()

    mock_download.side_effect = fake_download
    mock_uploader.upload_and_delete.side_effect = fake_upload_and_delete

    downloader = Downloader(cfg, state)
    downloader.run()

    out_dir = cfg.output_dir / "C1234" / str(cfg.current_year) / "116"
    mock_uploader.upload_and_delete.assert_called_once_with(out_dir, f"C1234/{cfg.current_year}/116")
    assert not list(out_dir.rglob("*"))


@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
@patch("mssp_pipeline.integration.downloader.build_remote_uploader")
def test_s3_marks_uploaded_in_state(mock_uploader_factory, mock_list, mock_view, mock_download, tmp_path):
    cfg = make_config(tmp_path, remote_store="s3://my-bucket")
    state = StateManager(cfg.state_file)

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = VIEW_TWO_FILES
    mock_uploader_factory.return_value = MagicMock()

    def fake_download(cli_path, aco, year, code, created_after=None):
        cfg.staging_dir.mkdir(parents=True, exist_ok=True)
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.ZCY25.D250122.T1621240.zip")
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.ZCY25.D250212.T1202370.zip")

    mock_download.side_effect = fake_download

    downloader = Downloader(cfg, state)
    downloader.run()

    assert state.is_uploaded("C1234", cfg.current_year, 116, "P.C1234.ACO.ZCY25.D250122.T1621240.zip", "2025-01-24T14:33:13.000Z")
    assert state.is_uploaded("C1234", cfg.current_year, 116, "P.C1234.ACO.ZCY25.D250212.T1202370.zip", "2025-02-12T22:13:12.000Z")

    record = state._data["downloaded"]["C1234"][str(cfg.current_year)]["116"]["P.C1234.ACO.ZCY25.D250122.T1621240.zip"]
    assert record["remote_prefix"] == f"C1234/{cfg.current_year}/116"
    assert record["s3_prefix"] == f"C1234/{cfg.current_year}/116"


@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
@patch("mssp_pipeline.integration.downloader.build_remote_uploader")
def test_s3_skips_already_uploaded_files(mock_uploader_factory, mock_list, mock_view, mock_download, tmp_path):
    cfg = make_config(tmp_path, remote_store="s3://my-bucket")
    state = StateManager(cfg.state_file)

    # Pre-populate state as already uploaded
    state.mark_uploaded("C1234", cfg.current_year, 116, "P.C1234.ACO.ZCY25.D250122.T1621240.zip", "2025-01-24T14:33:13.000Z", f"C1234/{cfg.current_year}/116")
    state.mark_uploaded("C1234", cfg.current_year, 116, "P.C1234.ACO.ZCY25.D250212.T1202370.zip", "2025-02-12T22:13:12.000Z", f"C1234/{cfg.current_year}/116")

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = VIEW_TWO_FILES
    mock_uploader_factory.return_value = MagicMock()

    downloader = Downloader(cfg, state)
    downloader.run()

    mock_download.assert_not_called()


@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
@patch("mssp_pipeline.integration.downloader.build_remote_uploader")
def test_s3_does_not_skip_downloaded_but_not_uploaded(mock_uploader_factory, mock_list, mock_view, mock_download, tmp_path):
    """A file marked as downloaded (but lacking uploaded_at) must be re-processed in S3 mode."""
    cfg = make_config(tmp_path, remote_store="s3://my-bucket")
    state = StateManager(cfg.state_file)

    # Mark as downloaded only — simulates a crash between download and upload
    state.mark_downloaded("C1234", cfg.current_year, 116, "P.C1234.ACO.ZCY25.D250122.T1621240.zip", "2025-01-24T14:33:13.000Z")

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = VIEW_ONE_FILE
    mock_uploader_factory.return_value = MagicMock()

    def fake_download(cli_path, aco, year, code, created_after=None):
        cfg.staging_dir.mkdir(parents=True, exist_ok=True)
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.ZCY25.D250122.T1621240.zip")

    mock_download.side_effect = fake_download

    downloader = Downloader(cfg, state)
    downloader.run()

    mock_download.assert_called_once()


@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
@patch("mssp_pipeline.integration.downloader.build_remote_uploader")
def test_azure_marks_uploaded_in_state(mock_uploader_factory, mock_list, mock_view, mock_download, tmp_path):
    cfg = make_config(tmp_path, remote_store="az://container/base")
    state = StateManager(cfg.state_file)

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = VIEW_ONE_FILE
    mock_uploader_factory.return_value = MagicMock()

    def fake_download(cli_path, aco, year, code, created_after=None):
        cfg.staging_dir.mkdir(parents=True, exist_ok=True)
        make_fake_zip(cfg.staging_dir, "P.C1234.ACO.ZCY25.D250122.T1621240.zip")

    mock_download.side_effect = fake_download

    Downloader(cfg, state).run()

    record = state._data["downloaded"]["C1234"][str(cfg.current_year)]["116"]["P.C1234.ACO.ZCY25.D250122.T1621240.zip"]
    assert record["remote_prefix"] == f"C1234/{cfg.current_year}/116"


@patch("mssp_pipeline.integration.downloader.run_download")
@patch("mssp_pipeline.integration.downloader.run_view")
@patch("mssp_pipeline.integration.downloader.run_list")
@patch("mssp_pipeline.integration.downloader.build_remote_uploader")
def test_gcs_skips_already_uploaded_files(mock_uploader_factory, mock_list, mock_view, mock_download, tmp_path):
    cfg = make_config(tmp_path, remote_store="gs://bucket/base")
    state = StateManager(cfg.state_file)
    state.mark_uploaded(
        "C1234",
        cfg.current_year,
        116,
        "P.C1234.ACO.ZCY25.D250122.T1621240.zip",
        "2025-01-24T14:33:13.000Z",
        f"C1234/{cfg.current_year}/116",
    )

    mock_list.return_value = LIST_ONE_CODE
    mock_view.return_value = VIEW_ONE_FILE
    mock_uploader_factory.return_value = MagicMock()

    Downloader(cfg, state).run()

    mock_download.assert_not_called()
