import logging
import re

# The single application logger — import this in every module
logger = logging.getLogger("canvas_sync")


class _RichMarkupStrippingFormatter(logging.Formatter):
    """Formatter that strips Rich markup tags (e.g. [bold], [/bold]) for plain-text output."""

    _MARKUP_RE = re.compile(r"\[/?[a-z][\w. ]*\]", re.IGNORECASE)

    def format(self, record):
        # Work on a copy so the Rich handler still sees markup
        original_msg = record.msg
        if isinstance(record.msg, str):
            record.msg = self._MARKUP_RE.sub("", record.msg)
        result = super().format(record)
        record.msg = original_msg
        return result


def setup_logging(verbose=False, quiet=False, log_file=None, to_stderr=False):
    """
    Configure logging for the application. Call once from main() after arg parsing.

    Args:
        verbose: Show DEBUG messages with timestamps and level labels.
        quiet: Only show ERROR messages.
        log_file: Optional path to write a full DEBUG log (plain text).
        to_stderr: Log to stderr instead of stdout, for a command whose stdout
            is a JSON document. Quiet alone still lets errors into stdout,
            where they break the document they were meant to explain.
    """
    from rich.console import Console
    from rich.logging import RichHandler

    # Determine console level
    if quiet:
        console_level = logging.ERROR
    elif verbose:
        console_level = logging.DEBUG
    else:
        console_level = logging.INFO

    # Root logger level must be the lowest of all handlers
    logger.setLevel(logging.DEBUG)

    # Console handler (Rich)
    rich_handler = RichHandler(
        console=Console(stderr=True) if to_stderr else None,
        level=console_level,
        show_time=verbose,
        show_path=False,
        show_level=verbose,
        markup=True,
        rich_tracebacks=True,
        tracebacks_show_locals=verbose,
    )
    rich_handler.setLevel(console_level)
    logger.addHandler(rich_handler)

    # File handler (optional, always DEBUG, plain text)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(_RichMarkupStrippingFormatter(
            "%(asctime)s [%(levelname)-7s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(file_handler)
