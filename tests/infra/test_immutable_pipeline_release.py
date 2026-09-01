"""Generic image-hardening and release-provenance contract.

These assertions are client-neutral: they check the *mechanism* (digest-pinned
base, frozen install, CMS binary verification, non-root runtime, immutable
release metadata) and use synthetic values only. No registry, account, backend,
or destination literal appears here.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERIFIER = ROOT / "scripts" / "verify_release_metadata.py"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_dockerfile_pins_base_by_digest_and_installs_frozen() -> None:
    dockerfile = read("Dockerfile")
    gitignore = read(".gitignore")

    assert re.search(
        r"^FROM python:3\.11-slim@sha256:[0-9a-f]{64}$",
        dockerfile,
        flags=re.MULTILINE,
    ), "base image must be pinned by digest"
    assert (ROOT / "uv.lock").is_file()
    # uv.lock must be committed (present, not ignored) so the frozen install is
    # a function of the checkout alone.
    assert not re.search(r"^uv\.lock$", gitignore, flags=re.MULTILINE)
    assert "COPY pyproject.toml uv.lock ./" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile


def test_dockerfile_preserves_the_multicloud_extras_seam() -> None:
    dockerfile = read("Dockerfile")
    # The set of installed backends stays a build-time seam, not a hardcoded
    # client choice; determinism comes from --frozen, not from fixed extras.
    assert "ARG PIP_EXTRAS=processing" in dockerfile
    assert "$PIP_EXTRAS" in dockerfile


def test_dockerfile_bakes_release_provenance() -> None:
    dockerfile = read("Dockerfile")
    for argument in ("SOURCE_COMMIT", "RELEASE_ID", "DEPENDENCY_CHECKSUM"):
        assert f"ARG {argument}" in dockerfile
        assert f"MSSP_{argument}=" in dockerfile
    assert "org.opencontainers.image.revision=" in dockerfile
    assert "org.opencontainers.image.version=" in dockerfile


def test_dockerfile_runs_as_non_root() -> None:
    dockerfile = read("Dockerfile")
    assert re.search(r"^USER\s+mssp\s*$", dockerfile, flags=re.MULTILINE)
    # The USER switch must be the last privilege-relevant instruction, i.e. no
    # RUN follows it.
    lines = [line.strip() for line in dockerfile.splitlines()]
    user_index = next(i for i, line in enumerate(lines) if line.startswith("USER "))
    assert not any(
        line.startswith("RUN ") for line in lines[user_index + 1 :]
    ), "no RUN may follow the USER switch"


def test_dockerfile_verifies_bundled_cms_binaries() -> None:
    dockerfile = read("Dockerfile")
    assert "sha256sum --check release/cms-binaries.sha256" in dockerfile


def test_cms_binary_checksums_match_the_committed_binaries() -> None:
    checksums = read("release/cms-binaries.sha256")
    entries = {}
    for line in checksums.splitlines():
        if not line.strip():
            continue
        recorded, relative = line.split(maxsplit=1)
        entries[relative.strip()] = recorded
    # Only the shipped Linux CLI is recorded. The macOS build is a local-dev
    # convenience, excluded from the image via .dockerignore and not verified at
    # release time (verifying it would fail the in-container check, since it is
    # deliberately absent from the build context).
    assert set(entries) == {"bin/acoms-cli-linux"}
    for relative, recorded in entries.items():
        assert re.fullmatch(r"[0-9a-f]{64}", recorded)
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert actual == recorded, f"{relative} checksum drifted from the file"


def test_dockerignore_is_retained() -> None:
    # A prior fork deleted .dockerignore; the generic image keeps it so the
    # build context stays small and deterministic.
    assert (ROOT / ".dockerignore").is_file()


def test_gitleaks_extends_the_default_ruleset() -> None:
    toml = read(".gitleaks.toml")
    assert "[extend]" in toml
    assert "useDefault = true" in toml
    # No private allowlist ships upstream.
    assert not (ROOT / ".gitleaksignore").exists()


def test_build_script_hardens_provenance_and_immutability() -> None:
    build = read("scripts/build-and-push-image.sh")
    assert "clean checkout" in build.lower()
    assert "shasum -a 256 -c release/cms-binaries.sha256" in build
    for argument in ("SOURCE_COMMIT", "RELEASE_ID", "DEPENDENCY_CHECKSUM"):
        assert f'--build-arg "{argument}=' in build
    assert "imageTagMutability" in build
    assert "aws ecr describe-images" in build
    assert "Refusing mutable image reference" in build
    # No mutable-tag discovery.
    assert "latest_taskdef_arn" not in build


def _write_metadata(path: Path, **overrides: object) -> Path:
    metadata = {
        "image": "registry.example/mssp-pipeline@sha256:" + "a" * 64,
        "source_commit": "b" * 40,
        "release_id": "example-1",
        "dependency_checksum": "c" * 64,
    }
    metadata.update(overrides)
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return path


def _run_verifier(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFIER), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_verifier_accepts_wellformed_metadata(tmp_path: Path) -> None:
    metadata = _write_metadata(tmp_path / "release.json")
    result = _run_verifier(str(metadata))
    assert result.returncode == 0, result.stderr


def test_verifier_rejects_mutable_image(tmp_path: Path) -> None:
    metadata = _write_metadata(
        tmp_path / "release.json", image="registry.example/mssp-pipeline:latest"
    )
    result = _run_verifier(str(metadata))
    assert result.returncode == 1
    assert "immutable" in result.stderr.lower()


def test_verifier_rejects_extra_fields(tmp_path: Path) -> None:
    metadata = _write_metadata(tmp_path / "release.json", command_contract={"x": 1})
    result = _run_verifier(str(metadata))
    assert result.returncode == 1
    assert "fields do not match" in result.stderr.lower()


def test_verifier_cross_checks_the_checkout(tmp_path: Path) -> None:
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    lock_checksum = hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
    metadata = _write_metadata(
        tmp_path / "release.json",
        source_commit=head,
        dependency_checksum=lock_checksum,
    )
    assert _run_verifier(str(metadata), "--repo", str(ROOT)).returncode == 0

    # A commit that is not HEAD is rejected against the checkout.
    wrong = _write_metadata(
        tmp_path / "wrong.json",
        source_commit="d" * 40,
        dependency_checksum=lock_checksum,
    )
    bad = _run_verifier(str(wrong), "--repo", str(ROOT))
    assert bad.returncode == 1
    assert "head" in bad.stderr.lower()
