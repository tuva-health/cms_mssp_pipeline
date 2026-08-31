"""The EvidenceSink interface and its development sinks.

An ``EvidenceSink`` is the append-only seam through which stages, resume,
readiness, and counts emit evidence records. The interface is deliberately
tiny — appending a validated record — so a durable acceptance sink can be
selected later without touching any emitter.

Two development sinks ship here: an in-memory sink for tests and an atomic
JSONL sink for local runs. Both are development-only; the durable acceptance
sink is chosen by the acceptance-evidence decision, not this package.
"""

from __future__ import annotations

import abc
import json
import os
from pathlib import Path
from typing import Iterable, Iterator, List

from mssp_pipeline.evidence.records import _Record, record_from_dict, validate_record


class EvidenceSink(abc.ABC):
    """Append-only sink for validated evidence records."""

    @abc.abstractmethod
    def append(self, record: _Record) -> None:
        """Validate and durably append a single record."""

    def extend(self, records: Iterable[_Record]) -> None:
        for record in records:
            self.append(record)


class InMemoryEvidenceSink(EvidenceSink):
    """A sink that keeps validated records in memory, in append order."""

    def __init__(self) -> None:
        self._records: List[_Record] = []

    def append(self, record: _Record) -> None:
        validate_record(record)
        self._records.append(record)

    def records(self) -> Iterator[_Record]:
        return iter(list(self._records))


class JsonlEvidenceSink(EvidenceSink):
    """An append-only JSONL sink for local development.

    Each record is validated, serialized to a single line, and appended with a
    lone ``write`` under ``O_APPEND`` so a record lands whole — the file is only
    ever grown, never rewritten in place.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: _Record) -> None:
        validate_record(record)
        line = json.dumps(record.to_dict(), sort_keys=True) + "\n"
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)

    def read(self) -> List[_Record]:
        """Reconstruct the typed records in append order."""
        if not self.path.exists():
            return []
        out: List[_Record] = []
        for line in self.path.read_text().splitlines():
            line = line.strip()
            if line:
                out.append(record_from_dict(json.loads(line)))
        return out
