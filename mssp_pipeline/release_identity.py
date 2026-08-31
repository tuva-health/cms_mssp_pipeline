"""Exact image / task / artifact identity matchers.

A deployable identity must resolve to exactly one artifact. These matchers and
verifiers encode that rule so a caller can reject mutable references before they
reach a plan or a task definition. The partition/service, registry, account, and
family names are the caller's data -- only the *shape* of an exact identity is
defined here.
"""

from __future__ import annotations

import re

# repository@sha256:<64 lowercase hex>
_IMMUTABLE_IMAGE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")
# arn:aws:ecs:<region>:<account>:task-definition/<family>:<revision>
_EXACT_TASK_REVISION = re.compile(
    r"arn:aws:ecs:[a-z0-9-]+:\d{12}:task-definition/[^:/\s]+:\d+"
)
_FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def is_immutable_image(value: str) -> bool:
    """True iff ``value`` is an image pinned by @sha256 digest."""
    return bool(_IMMUTABLE_IMAGE.fullmatch(value))


def is_exact_task_revision(value: str) -> bool:
    """True iff ``value`` is an ECS task-definition ARN with an exact revision."""
    return bool(_EXACT_TASK_REVISION.fullmatch(value))


def is_full_commit(value: str) -> bool:
    """True iff ``value`` is a full 40-character lowercase-hex commit id."""
    return bool(_FULL_COMMIT.fullmatch(value))


def is_sha256(value: str) -> bool:
    """True iff ``value`` is a bare sha256 hex digest."""
    return bool(_SHA256.fullmatch(value))


def verify_image(actual: str, expected: str) -> None:
    """Raise unless ``actual`` is an immutable digest equal to ``expected``."""
    if not is_immutable_image(actual):
        raise ValueError(f"image {actual!r} is not an immutable digest")
    if not is_immutable_image(expected):
        raise ValueError(f"expected image {expected!r} is not an immutable digest")
    if actual != expected:
        raise ValueError(f"image {actual!r} does not match expected {expected!r}")


def verify_task_revision(actual: str, expected: str) -> None:
    """Raise unless ``actual`` is an exact task revision equal to ``expected``."""
    if not is_exact_task_revision(actual):
        raise ValueError(f"task {actual!r} does not select an exact revision")
    if not is_exact_task_revision(expected):
        raise ValueError(f"expected task {expected!r} does not select an exact revision")
    if actual != expected:
        raise ValueError(f"task {actual!r} does not match expected {expected!r}")
