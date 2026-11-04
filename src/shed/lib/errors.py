from __future__ import annotations

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


class ShedError(Exception):
    """Base class for expected failures. The CLI prints the message and exits with ``exit_code``."""

    exit_code: int = EXIT_FAILURE


class ToolError(ShedError):
    """A tool failed in an expected way. Raise it for a clean message without a traceback."""

    exit_code = EXIT_FAILURE


class UsageError(ShedError):
    """The invocation was wrong: unknown tool, unknown flag or invalid input."""

    exit_code = EXIT_USAGE
