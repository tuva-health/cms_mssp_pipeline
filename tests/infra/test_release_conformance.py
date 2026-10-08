"""``scripts/check-release-conformance.sh`` proves a fork carries a release.

A fork conforms to release ``vX.Y.Z`` when the tag is an ancestor of the fork's
commit and the two trees differ only in client-specific paths (the overlay
under ``infra/clients/<client>/`` and ``.gitignore``). The script runs for real
against a throwaway git repository under ``tmp_path``; synthetic names only.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check-release-conformance.sh"
TAG = "v1.2.3"
# Canonical .gitignore keeps overlays out of git except the example.
CANONICAL_IGNORE = "infra/clients/*\n!infra/clients/client.example/\n!infra/clients/client.example/**\n"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # Hermetic git: no user/system config (signing, hooks, identity guards).
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.com",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.com",
    )
    return env


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, env=_env(), check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> None:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _check(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=repo, env=_env(), capture_output=True, text=True
    )


@pytest.fixture
def fork(tmp_path: Path) -> Path:
    """A canonical history tagged TAG, merged into a fork's main through upstream-sync."""
    repo = tmp_path / "fork"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(
        repo,
        {
            "mssp_pipeline/app.py": "v0\n",
            "infra/clients/client.example/env.sh.example": "x\n",
            ".gitignore": CANONICAL_IGNORE,
        },
        "canonical v0",
    )
    _git(repo, "switch", "-q", "-c", "fork-main")
    _commit(
        repo,
        {"infra/clients/acme/env.sh": "fork\n", ".gitignore": CANONICAL_IGNORE + "!infra/clients/acme/\n"},
        "fork commits its overlay",
    )
    _git(repo, "switch", "-q", "main")
    _commit(repo, {"mssp_pipeline/app.py": "v1\n"}, "canonical v1")
    _git(repo, "tag", "-a", TAG, "-m", TAG)
    _git(repo, "switch", "-q", "fork-main")
    _git(repo, "merge", "-q", "--no-ff", TAG, "-m", f"Merge {TAG}")
    return repo


def test_pass_when_only_client_paths_differ(fork: Path) -> None:
    result = _check(fork, TAG)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
    assert "  infra/clients/acme/env.sh" in result.stdout
    assert "  .gitignore" in result.stdout


def test_fail_names_canonical_paths_the_fork_changed(fork: Path) -> None:
    _commit(
        fork,
        {"mssp_pipeline/app.py": "patched\n", "infra/clients/client.example/env.sh.example": "y\n"},
        "fork patches canonical code",
    )
    result = _check(fork, TAG)
    assert result.returncode == 1
    assert "FAIL" in result.stdout
    assert "  mssp_pipeline/app.py" in result.stdout
    assert "  infra/clients/client.example/env.sh.example" in result.stdout
    assert "infra/clients/acme/env.sh" not in result.stdout


def test_fail_when_tag_is_not_an_ancestor(fork: Path) -> None:
    # A rebase-style copy of the tag's content without the tag in history.
    _git(fork, "switch", "-q", "--orphan", "rebased")
    _commit(fork, {"mssp_pipeline/app.py": "v1\n"}, "copied content")
    result = _check(fork, TAG)
    assert result.returncode == 1
    assert "not an ancestor" in result.stdout


def test_fail_when_head_carries_unreleased_upstream_commits(fork: Path) -> None:
    _git(fork, "switch", "-q", "main")
    _commit(fork, {"mssp_pipeline/app.py": "v2-unreleased\n"}, "canonical after tag")
    _git(fork, "switch", "-q", "fork-main")
    _git(fork, "merge", "-q", "--no-ff", "main", "-m", "Merge main tip")
    result = _check(fork, TAG)
    assert result.returncode == 1
    assert "  mssp_pipeline/app.py" in result.stdout


def test_fail_when_tag_is_missing(fork: Path) -> None:
    result = _check(fork, "v9.9.9")
    assert result.returncode == 1
    assert "no tag v9.9.9" in result.stdout


def test_explicit_rev_is_checked(fork: Path) -> None:
    _commit(fork, {"mssp_pipeline/app.py": "patched\n"}, "fork patches canonical code")
    assert _check(fork, TAG, "HEAD~1").returncode == 0
    assert _check(fork, TAG, "HEAD").returncode == 1
