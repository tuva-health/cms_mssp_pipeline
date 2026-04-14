"""Run manifest helpers for orchestration observability and resume support."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    tmp.replace(path)


def latest_run_id(manifest_dir: Path | str = ".runs") -> str | None:
    base = Path(manifest_dir)
    if not base.exists():
        return None
    candidates = [p for p in base.glob("*.json") if p.is_file()]
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return latest.stem


class RunManifest:
    def __init__(self, run_id: str, manifest_dir: Path | str = ".runs"):
        self.run_id = run_id
        self.manifest_dir = Path(manifest_dir)
        self.path = self.manifest_dir / f"{run_id}.json"
        self.data: dict = {
            "run_id": run_id,
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

    def finalize(self, status: str) -> None:
        self.data["status"] = status
        self.data["ended_at"] = _utc_now()

    def save(self) -> None:
        _atomic_write(self.path, json.dumps(self.data, indent=2))
