"""Artifact identity and digest helpers for the run-evidence core.

Evidence records are client-neutral: they carry *logical identifiers* and
*digests*, never concrete commands or destinations. This module is the single
place that defines what a logical id may contain and how a digest is computed,
so every record type and validator enforces the same rule.
"""

from __future__ import annotations

import hashlib
import re


# A logical id names a stage, prerequisite, metric, artifact, or parameter in
# the abstract. It deliberately excludes anything that could smuggle a
# destination (``://``), a path (``/``), or a command (whitespace/shell
# separators): those are A-class policy and must never enter the evidence core.
LOGICAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def digest(value: str | bytes) -> str:
    """Return the hex SHA-256 of ``value``.

    Callers pass values through here before putting them in a record, so the
    record proves *what* was seen without exposing the concrete value.
    """
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def is_digest(value: object) -> bool:
    """True iff ``value`` is a lowercase hex SHA-256 string."""
    return isinstance(value, str) and bool(_HEX_SHA256.fullmatch(value))


def validate_logical_id(value: str) -> str:
    """Return ``value`` unchanged if it is a safe logical id, else raise.

    Rejects empty ids and any id containing a scheme, path separator, or
    whitespace — the shapes a concrete destination or command would take.
    """
    if not isinstance(value, str) or not LOGICAL_ID_PATTERN.fullmatch(value):
        raise ValueError(
            f"Invalid logical id {value!r}: must be non-empty and use only "
            "letters, numbers, dot, underscore, or hyphen (no destinations or commands)"
        )
    return value
