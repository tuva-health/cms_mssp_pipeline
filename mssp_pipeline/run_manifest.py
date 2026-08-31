"""Run manifest — a projection over the append-only run-evidence core.

The manifest is no longer the authority for what happened in a run; the
evidence records are (see :mod:`mssp_pipeline.evidence`). A phase's terminal
outcome is expressed as an evidence record — ``StageAttempted`` for a phase this
run performed, ``StageReused`` for a phase satisfied by a prior run — and the
familiar ``.runs/<run_id>.json`` view is *derived* from those records plus the
manifest's own transient lifecycle facts (running state, operator-requested
skips, timestamps, error text, params, events).

The public surface and on-disk shape are unchanged, so existing consumers and
the manifest lineage tests keep working. When an :class:`EvidenceSink` is
attached, every emitted record also flows to that durable, append-only log.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from mssp_pipeline.evidence.identity import digest
from mssp_pipeline.evidence.records import RunStarted, StageAttempted, StageReused, _Record
from mssp_pipeline.evidence.reuse import build_stage_reused


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


# Phase-level lifecycle facts the evidence records do not carry. Terminal
# completion status is always taken from a record; the overlay holds only the
# transient status and human-facing annotations.
_TERMINAL = {"completed", "failed", "skipped"}


class RunManifest:
    def __init__(self, run_id: str, manifest_dir: Path | str = ".runs", *, sink=None):
        self.run_id = validate_run_id(run_id)
        self.manifest_dir = Path(manifest_dir)
        self.path = self.manifest_dir / f"{self.run_id}.json"
        self.sink = sink

        # The evidence core for this run, plus the transient lifecycle facts the
        # records do not model. ``data`` is the projection folded from both.
        self._records: list[_Record] = []
        self._status = "running"
        self._started_at = _utc_now()
        self._ended_at: str | None = None
        self._params: dict = {}
        self._events: list[dict] = []
        # phase -> {started_at?, ended_at?, status?(transient), error?, details?}
        self._phase_overlay: dict[str, dict] = {}
        self._loaded = False

        self.data: dict = {}
        self._project()

    # -- record emission ---------------------------------------------------

    def _emit(self, record: _Record) -> None:
        self._records.append(record)
        if self.sink is not None:
            self.sink.append(record)

    def evidence_records(self) -> list[_Record]:
        """The append-only evidence records this manifest has emitted, in order."""
        return list(self._records)

    # -- projection --------------------------------------------------------

    def _project(self) -> None:
        """Rebuild ``self.data`` from the evidence records plus the overlay.

        Terminal phase status comes from the records (a ``StageAttempted`` gives
        completed/failed; a ``StageReused`` gives a reuse skip); the overlay
        supplies transient status, timestamps, error text, and details.
        """
        if self._loaded:
            return

        phases: dict[str, dict] = {}
        for phase, overlay in self._phase_overlay.items():
            entry: dict = {}
            if "started_at" in overlay:
                entry["started_at"] = overlay["started_at"]
            if "ended_at" in overlay:
                entry["ended_at"] = overlay["ended_at"]
            if "status" in overlay:  # transient (running) or operator skip
                entry["status"] = overlay["status"]
            if "error" in overlay:
                entry["error"] = overlay["error"]
            if "details" in overlay:
                entry["details"] = overlay["details"]
            phases[phase] = entry

        # Terminal outcomes are authoritative from the evidence records.
        for record in self._records:
            if isinstance(record, StageAttempted):
                entry = phases.setdefault(record.stage_id, {})
                entry["status"] = "completed" if record.outcome == "succeeded" else "failed"
            elif isinstance(record, StageReused):
                entry = phases.setdefault(record.stage_id, {})
                entry["status"] = "skipped"

        self.data = {
            "run_id": self.run_id,
            "status": self._status,
            "started_at": self._started_at,
            "ended_at": self._ended_at,
            "params": self._params,
            "phases": phases,
            "events": self._events,
        }

    @classmethod
    def load(cls, run_id: str, manifest_dir: Path | str = ".runs") -> "RunManifest":
        inst = cls(run_id, manifest_dir=manifest_dir)
        inst.data = json.loads(inst.path.read_text())
        inst._loaded = True  # a loaded manifest is read-only; do not re-project
        return inst

    # -- mutation API (unchanged surface) ----------------------------------

    def set_params(self, **params: object) -> None:
        self._params = params
        # The run root records only digests of the parameter values, so a
        # destination or secret never enters the evidence graph.
        self._emit(
            RunStarted(
                run_id=self.run_id,
                occurred_at=_utc_now(),
                param_digests={name: digest(str(value)) for name, value in params.items()},
            )
        )
        self._project()

    def add_event(self, level: str, message: str, *, phase: str | None = None, **context: object) -> None:
        self._events.append(
            {
                "time": _utc_now(),
                "level": level,
                "message": message,
                "phase": phase,
                "context": context or None,
            }
        )
        self._project()

    def set_phase(self, phase: str, status: str, *, error: str | None = None, details: dict | None = None) -> None:
        overlay = self._phase_overlay.setdefault(phase, {})
        if "started_at" not in overlay and status == "running":
            overlay["started_at"] = _utc_now()
        if status in _TERMINAL:
            overlay.setdefault("started_at", _utc_now())
            overlay["ended_at"] = _utc_now()
        if error:
            overlay["error"] = error
        if details:
            overlay["details"] = details

        if status == "completed":
            overlay.pop("status", None)  # status now derives from the record
            self._emit(StageAttempted(run_id=self.run_id, occurred_at=_utc_now(), stage_id=phase, outcome="succeeded"))
        elif status == "failed":
            overlay.pop("status", None)
            self._emit(StageAttempted(run_id=self.run_id, occurred_at=_utc_now(), stage_id=phase, outcome="failed"))
        elif status == "skipped" and details and details.get("reason") == "resume" and details.get("satisfied_by"):
            overlay.pop("status", None)  # a reuse skip derives from the record
            self._emit(
                build_stage_reused(
                    run_id=self.run_id,
                    occurred_at=_utc_now(),
                    stage_id=phase,
                    reused_from_run_id=str(details["satisfied_by"]),
                )
            )
        else:
            # Transient (running) or an operator-requested skip: overlay only.
            overlay["status"] = status

        self._project()

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
        self._status = status
        self._ended_at = _utc_now()
        self._project()

    def save(self) -> None:
        _atomic_write(self.path, json.dumps(self.data, indent=2))
