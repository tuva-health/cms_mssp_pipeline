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
    # scripts/build-image.sh is the one build recipe; the ECR push wrapper and
    # the tag-triggered release workflow both call it.
    build = read("scripts/build-image.sh")
    assert "clean checkout" in build.lower()
    assert "shasum -a 256 -c release/cms-binaries.sha256" in build
    for argument in ("SOURCE_COMMIT", "RELEASE_ID", "DEPENDENCY_CHECKSUM", "SOURCE_DATE_EPOCH"):
        assert f'--build-arg "{argument}=' in build
    assert "--platform linux/amd64" in build
    # Reproducible digests: no timestamped attestations, layer times clamped.
    assert "--provenance=false" in build
    assert "--sbom=false" in build
    assert "rewrite-timestamp=true" in build
    assert "Refusing mutable image reference" in build

    push = read("scripts/build-and-push-image.sh")
    assert 'scripts/build-image.sh" "$REPOSITORY" "$RELEASE_ID"' in push
    assert "--push" in push
    assert "docker buildx build" not in push, "the push wrapper must not fork the build"
    assert "imageTagMutability" in push
    assert "aws ecr describe-images" in push
    assert '"$DIGEST" != "$BUILT_DIGEST"' in push
    # No mutable-tag discovery.
    assert "latest_taskdef_arn" not in push


def test_release_workflow_builds_without_publishing() -> None:
    workflow = read(".github/workflows/release.yml")
    assert "scripts/build-image.sh mssp-pipeline" in workflow
    assert "--push" not in workflow
    assert "scripts/verify_release_metadata.py" in workflow
    assert "--prerelease" in workflow
    # No registry login of any kind: the release publishes digests, not images.
    for registry_step in ("docker/login-action", "aws-actions/", "ghcr.io", "packages: write"):
        assert registry_step not in workflow


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


NOTES = ROOT / "scripts" / "release_notes.py"


def _record(directory: Path, variant: str, config: str, manifest: str) -> None:
    record = {
        "image": "mssp-pipeline@sha256:" + manifest * 64,
        "manifest_digest": "sha256:" + manifest * 64,
        "config_digest": "sha256:" + config * 64,
        "source_commit": "b" * 40,
        "release_id": "v0.2.0",
        "dependency_checksum": "c" * 64,
        "pip_extras": "processing",
        "source_date_epoch": 1,
        "platform": "linux/amd64",
    }
    (directory / f"build-record-v0.2.0-{variant}.json").write_text(json.dumps(record))


def _notes(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(NOTES), *args], text=True, capture_output=True, check=False
    )


def test_release_notes_require_a_changelog_section() -> None:
    assert _notes("changelog", "0.2.0").returncode == 0
    assert _notes("changelog", "99.0.0").returncode != 0


def test_release_notes_name_the_contract_and_refuse_an_unreproduced_digest(
    tmp_path: Path,
) -> None:
    _record(tmp_path, "parquet", "1", "2")
    _record(tmp_path, "parquet-rebuild", "1", "2")
    matched = _notes("notes", "--tag", "v0.2.0", "--records", str(tmp_path), "--require-match")
    assert matched.returncode == 0, matched.stderr
    assert "cms-mssp-workbook-export" in matched.stdout
    assert "sha256:" + "1" * 64 in matched.stdout
    assert "## Changelog" in matched.stdout
    # Digests are Tuva's reproducibility record; clients adopt via the consumption doc.
    assert "blob/v0.2.0/docs/client-release-consumption.md" in matched.stdout
    assert "conformance check" in matched.stdout
    assert "build your own" not in matched.stdout

    _record(tmp_path, "parquet-rebuild", "3", "2")
    drifted = _notes("notes", "--tag", "v0.2.0", "--records", str(tmp_path), "--require-match")
    assert drifted.returncode == 3
    assert "**differ**" in drifted.stdout
