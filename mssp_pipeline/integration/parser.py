"""Parse text output from acoms-cli --list and --view commands."""

import re
from dataclasses import dataclass
from datetime import date


@dataclass
class FileEntry:
    filename: str
    last_updated: str  # ISO 8601 string as returned by the CLI

    def creation_date(self) -> date:
        """Extract creation date from the filename's D-component.

        Handles both 6-digit (YYMMDD, e.g. D250122 → 2025-01-22) and
        8-digit (YYYYMMDD, e.g. D20250122 → 2025-01-22) formats.
        """
        match = re.search(r"\.D(\d{6,8})\.", self.filename)
        if not match:
            raise ValueError(f"Cannot parse creation date from filename: {self.filename}")
        raw = match.group(1)
        if len(raw) == 8:
            year, month, day = int(raw[:4]), int(raw[4:6]), int(raw[6:8])
        else:
            year, month, day = 2000 + int(raw[:2]), int(raw[2:4]), int(raw[4:6])
        return date(year, month, day)


def parse_list(output: str) -> list[int]:
    """Return list of integer file codes from --list output.

    Each relevant line looks like:
        -----Assignment Report, Code 116
    """
    codes = []
    for line in output.splitlines():
        match = re.search(r"Code\s+(\d+)", line)
        if match:
            codes.append(int(match.group(1)))
    return codes


def parse_view(output: str) -> list[FileEntry]:
    """Return list of FileEntry from --view output.

    Each relevant line looks like:
        1 of 12 - P.C1234.ACO.ZCY25.D250122.T1621240.zip (925.93 MB) Last Updated: 2025-01-24T14:33:13.000Z
    """
    entries = []
    pattern = re.compile(
        r"\d+ of \d+ - (\S+)\s+\([^)]+\)\s+Last Updated:\s+(\S+)"
    )
    for line in output.splitlines():
        match = pattern.search(line)
        if match:
            entries.append(FileEntry(filename=match.group(1), last_updated=match.group(2)))
    return entries
