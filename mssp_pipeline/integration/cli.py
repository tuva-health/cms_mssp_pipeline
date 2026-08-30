"""Thin wrapper around the acoms-cli subprocess."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

# Network errors that are safe to retry (transient connection resets / rate limits).
_RETRYABLE_ERRORS = ("ECONNRESET", "ETIMEDOUT", "ECONNREFUSED")
# Gateway/server errors that require a longer back-off before retrying.
_GATEWAY_ERRORS = ("502",)
_MAX_RETRIES = 3
_RETRY_DELAY = 10   # seconds between retry attempts for connection errors
_GATEWAY_RETRY_DELAY = 60  # seconds to wait after a 502 gateway error
_REQUEST_DELAY = 3  # seconds to pause after every successful CLI call
_DEFAULT_TIMEOUT = 1800  # seconds; cap any single acoms-cli invocation


class CLIError(Exception):
    pass


def _cli_timeout() -> int:
    raw = os.environ.get("MSSP_ACOMS_CLI_TIMEOUT", "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT
    return value if value > 0 else _DEFAULT_TIMEOUT


def run_cmd(cli_path: Path, args: list[str], cwd: Path | None = None) -> str:
    """Run acoms-cli with the given arguments and return stdout as a string.

    Retries up to _MAX_RETRIES times on transient network errors (e.g. ECONNRESET),
    gateway errors (502), and subprocess timeouts. Each invocation is bounded by
    MSSP_ACOMS_CLI_TIMEOUT seconds (default 1800) so a hung CLI cannot stall the
    pipeline indefinitely.
    """
    cmd = [str(cli_path)] + args
    timeout = _cli_timeout()
    last_error: CLIError | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(cwd) if cwd else None,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            error = CLIError(
                f"acoms-cli exceeded MSSP_ACOMS_CLI_TIMEOUT={timeout}s: {' '.join(cmd)}"
            )
            if attempt < _MAX_RETRIES:
                print(f"  Timeout after {timeout}s; retrying in {_RETRY_DELAY}s (attempt {attempt}/{_MAX_RETRIES})...")
                time.sleep(_RETRY_DELAY)
                last_error = error
                continue
            raise error from exc
        if result.returncode == 0:
            time.sleep(_REQUEST_DELAY)
            return result.stdout
        stderr = result.stderr or result.stdout
        error = CLIError(
            f"acoms-cli exited with code {result.returncode}:\n{stderr}"
        )
        if attempt < _MAX_RETRIES:
            if any(e in stderr for e in _GATEWAY_ERRORS):
                print(f"  Gateway error (502); retrying in {_GATEWAY_RETRY_DELAY}s (attempt {attempt}/{_MAX_RETRIES})...")
                time.sleep(_GATEWAY_RETRY_DELAY)
                last_error = error
                continue
            if any(e in stderr for e in _RETRYABLE_ERRORS):
                print(f"  Network error ({stderr.strip()}); retrying in {_RETRY_DELAY}s (attempt {attempt}/{_MAX_RETRIES})...")
                time.sleep(_RETRY_DELAY)
                last_error = error
                continue
        raise error
    raise last_error  # type: ignore[misc]


def run_configure(cli_path: Path) -> None:
    """Run acoms-cli configure interactively, passing stdin/stdout through to the terminal."""
    result = subprocess.run([str(cli_path), "configure"])
    if result.returncode != 0:
        raise CLIError(f"acoms-cli configure exited with code {result.returncode}")


def run_list(cli_path: Path, aco: str, year: int) -> str:
    return run_cmd(cli_path, ["datahub", f"--aco={aco}", f"--year={year}", "--list"])


def run_view(cli_path: Path, aco: str, year: int, file_code: int) -> str:
    return run_cmd(
        cli_path,
        ["datahub", f"--aco={aco}", f"--year={year}", f"--file={file_code}", "--view"],
    )


def run_download(
    cli_path: Path,
    aco: str,
    year: int,
    file_code: int,
    created_after: str | None = None,
) -> str:
    """Download files for a given aco/year/file_code into the current working directory.

    The CLI requires config.txt to be present in cwd, so this must be called
    from the project root. Caller is responsible for moving the resulting zip
    files to the desired output location.
    """
    args = [
        "datahub",
        f"--aco={aco}",
        f"--year={year}",
        f"--file={file_code}",
        "--download",
    ]
    if created_after:
        args.append(f"--createdAfter={created_after}")
    return run_cmd(cli_path, args)
