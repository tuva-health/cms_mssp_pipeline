"""Processing-specific exception types."""


class ProcessingError(Exception):
    """Base class for processing pipeline errors."""


class ProcessingStartupError(ProcessingError):
    """Raised when processing initialization fails before any table runs."""


class SourceDiscoveryError(ProcessingError):
    """Raised when a processor cannot list its source files.

    Distinct from finding none. An unreachable prefix, a wrong bucket or expired
    credentials all make the listing itself fail, and treating that as "no files"
    lets a run skip every table and still report success.
    """


# DuckDB is inconsistent about an empty glob. A local pattern that matches
# nothing returns no rows, while zipfs and some remote filesystems raise
# IOException carrying this text. Both mean the same thing: the listing ran and
# found nothing. Any other IOException means the listing itself failed.
_NO_MATCH_MARKER = "no files found that match the pattern"


def is_empty_glob(error: BaseException) -> bool:
    """True when a DuckDB IOException means "nothing matched", not "could not list"."""
    return _NO_MATCH_MARKER in str(error).lower()
