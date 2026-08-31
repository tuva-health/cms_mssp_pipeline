"""Exact image / task / artifact identity verification (synthetic).

The rule everywhere is: a deployable identity must be pinned exactly -- an image
by @sha256 digest (never a mutable tag), an ECS task by :revision (never a bare
family), a source by full commit, an artifact by sha256. Registry/account/family
names in the examples are synthetic.
"""

from __future__ import annotations

import pytest

from mssp_pipeline.release_identity import (
    is_exact_task_revision,
    is_full_commit,
    is_immutable_image,
    is_sha256,
    verify_image,
)

DIGEST = "a" * 64


def test_immutable_image_requires_digest() -> None:
    assert is_immutable_image(f"registry.example/app@sha256:{DIGEST}")
    assert not is_immutable_image("registry.example/app:latest")
    assert not is_immutable_image("registry.example/app")
    assert not is_immutable_image(f"registry.example/app@sha256:{'a' * 63}")


def test_exact_task_revision_requires_numeric_revision() -> None:
    assert is_exact_task_revision(
        "arn:aws:ecs:us-east-1:123456789012:task-definition/app-download:7"
    )
    # A bare family (no :revision) is mutable and rejected.
    assert not is_exact_task_revision(
        "arn:aws:ecs:us-east-1:123456789012:task-definition/app-download"
    )
    assert not is_exact_task_revision("app-download")


def test_full_commit_and_sha256() -> None:
    assert is_full_commit("b" * 40)
    assert not is_full_commit("b" * 7)  # short commit
    assert is_sha256("c" * 64)
    assert not is_sha256("c" * 63)


def test_verify_image_matches_expected_immutable_digest() -> None:
    image = f"registry.example/app@sha256:{DIGEST}"
    verify_image(image, image)  # no raise


def test_verify_image_rejects_mutable_actual() -> None:
    with pytest.raises(ValueError, match="immutable"):
        verify_image("registry.example/app:latest", f"registry.example/app@sha256:{DIGEST}")


def test_verify_image_rejects_mismatch() -> None:
    with pytest.raises(ValueError, match="does not match"):
        verify_image(
            f"registry.example/app@sha256:{DIGEST}",
            f"registry.example/app@sha256:{'b' * 64}",
        )
