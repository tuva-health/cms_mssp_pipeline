"""Run manifest helpers for orchestration observability and resume support."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_url(value: str | None) -> str | None:
    """Strip query strings and userinfo from a URL before persisting it.

    Run manifests are written to disk and shipped with the project; remote
    store URIs occasionally include credentials (Azure SAS tokens in the query
    string, basic-auth userinfo in the netloc). Path-only URIs and non-URL
    strings pass through unchanged.
    """
    if not value or "://" not in value:
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    netloc = parts.netloc
    if "@" in netloc:
        host = netloc.rsplit("@", 1)[1]
        netloc = f"***@{host}"
    if not parts.query and netloc == parts.netloc:
        return value
    redacted_query = "<redacted>" if parts.query else ""
    return urlunsplit((parts.scheme, netloc, parts.path, redacted_query, parts.fragment))


def validate_run_id(run_id: str) -> str:
    if not run_id or not run_id.strip():
        raise ValueError("Invalid run id: must be non-empty and use only letters, numbers, dot, underscore, or hyphen")
    if run_id != run_id.strip() or ".." in run_id or "/" in run_id or "\\" in run_id or not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("Invalid run id: must use only letters, numbers, dot, underscore, or hyphen")
    return run_id


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    tmp.replace(path)


def latest_run_id(manifest_dir: Path | str = ".runs") -> str | None:
    base = Path(manifest_dir)
    if not base.exists():
        return None
    candidates = []
    for path in base.glob("*.json"):
        if not path.is_file():
            continue
        try:
            validate_run_id(path.stem)
        except ValueError:
            continue
        candidates.append(path)
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return latest.stem


class RunManifest:
    def __init__(self, run_id: str, manifest_dir: Path | str = ".runs"):
        self.run_id = validate_run_id(run_id)
        self.manifest_dir = Path(manifest_dir)
        self.path = self.manifest_dir / f"{self.run_id}.json"
        self.data: dict = {
            "run_id": self.run_id,
            "status": "running",
            "started_at": _utc_now(),
            "ended_at": None,
            "params": {},
            "phases": {},
            "events": [],
        }

    @classmethod
    def load(cls, run_id: str, manifest_dir: Path | str = ".runs") -> "RunManifest":
        inst = cls(run_id, manifest_dir=manifest_dir)
        inst.data = json.loads(inst.path.read_text())
        return inst

    def set_params(self, **params: object) -> None:
        self.data["params"] = params

    def add_event(self, level: str, message: str, *, phase: str | None = None, **context: object) -> None:
        event = {
            "time": _utc_now(),
            "level": level,
            "message": message,
            "phase": phase,
            "context": context or None,
        }
        self.data.setdefault("events", []).append(event)

    def set_phase(self, phase: str, status: str, *, error: str | None = None, details: dict | None = None) -> None:
        entry = self.data.setdefault("phases", {}).setdefault(phase, {})
        if "started_at" not in entry and status == "running":
            entry["started_at"] = _utc_now()
        if status in {"completed", "failed", "skipped"}:
            entry.setdefault("started_at", _utc_now())
            entry["ended_at"] = _utc_now()
        entry["status"] = status
        if error:
            entry["error"] = error
        if details:
            entry["details"] = details

    def phase_status(self, phase: str) -> str | None:
        return self.data.get("phases", {}).get(phase, {}).get("status")

    def phase_details(self, phase: str) -> dict:
        return self.data.get("phases", {}).get(phase, {}).get("details") or {}

    def phase_satisfied_by(self, phase: str) -> str | None:
        """Return the run that actually performed this phase, if any.

        A phase this run completed itself is satisfied by this run. A phase
        skipped because an earlier run in a resume chain had already done it
        carries that run's id forward, so a chain of resumes still points at
        the run that did the work.
        """
        if self.phase_status(phase) == "completed":
            return self.run_id
        if self.phase_status(phase) == "skipped":
            satisfied_by = self.phase_details(phase).get("satisfied_by")
            return str(satisfied_by) if satisfied_by else None
        return None

    def finalize(self, status: str) -> None:
        self.data["status"] = status
        self.data["ended_at"] = _utc_now()

    def save(self) -> None:
        _atomic_write(self.path, json.dumps(self.data, indent=2))
