"""Processing-specific exception types."""


class ProcessingError(Exception):
    """Base class for processing pipeline errors."""


class ProcessingStartupError(ProcessingError):
    """Raised when processing initialization fails before any table runs."""
