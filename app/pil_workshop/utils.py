"""Small, platform-agnostic helpers used across notebooks and the app.

Nothing here imports ``dbutils`` at module load. Widget/environment resolution
degrades gracefully when running off-platform (returns the provided default),
which keeps ``python -m compileall`` and unit tests happy.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

T = TypeVar("T")

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def get_logger(name: str = "pil_workshop") -> logging.Logger:
    """Return a module logger with a single stdout handler (idempotent)."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


LOG = get_logger()


def _get_dbutils() -> Any | None:
    """Best-effort fetch of the ambient ``dbutils`` handle inside a notebook."""
    try:  # pragma: no cover - only meaningful on Databricks
        import IPython

        ip = IPython.get_ipython()
        if ip is not None and "dbutils" in ip.user_ns:
            return ip.user_ns["dbutils"]
    except Exception:  # noqa: BLE001 - any failure just means "not in a notebook"
        pass
    return globals().get("dbutils")


def resolve(name: str, default: str, *, dbutils: Any | None = None) -> str:
    """Resolve a parameter from a notebook widget, then env var, then default.

    Resolution order: an explicit ``dbutils`` widget value → the
    ``PIL_<NAME>`` environment variable → ``default``. Empty strings are
    treated as unset so a blank widget falls through to the default.
    """
    du = dbutils or _get_dbutils()
    if du is not None:
        try:  # pragma: no cover - notebook-only path
            val = du.widgets.get(name)
            if val is not None and str(val).strip():
                return str(val).strip()
        except Exception:  # noqa: BLE001 - widget not defined yet
            pass
    env = os.environ.get(f"PIL_{name.upper()}")
    if env and env.strip():
        return env.strip()
    return default


def env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean-ish environment variable (``1/true/yes/on``)."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def retry(
    attempts: int = 3,
    delay: float = 1.5,
    backoff: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator: retry a callable with exponential backoff.

    Used to smooth over transient control-plane hiccups when creating assets
    (serving endpoints, Lakebase, Genie spaces) via the SDK.
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            wait = delay
            last: BaseException | None = None
            for i in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:  # noqa: BLE001 - configurable set
                    last = exc
                    if i == attempts:
                        break
                    LOG.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                        fn.__name__,
                        i,
                        attempts,
                        exc,
                        wait,
                    )
                    time.sleep(wait)
                    wait *= backoff
            assert last is not None
            raise last

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Pretty console output for the setup notebooks
# ---------------------------------------------------------------------------
def banner(title: str, char: str = "=", width: int = 78) -> None:
    """Print a section banner to stdout."""
    line = char * width
    print(f"\n{line}\n  {title}\n{line}")


def step(msg: str, status: str = "•") -> None:
    """Print a single step line (``status`` is a short glyph/label)."""
    print(f"  [{status}] {msg}")


def ok(msg: str) -> None:
    step(msg, "OK")


def warn(msg: str) -> None:
    step(msg, "WARN")


def skip(msg: str) -> None:
    step(msg, "SKIP")


def fail(msg: str) -> None:
    step(msg, "FAIL")


def summary_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    """Render a list of dict rows as a fixed-width text table."""
    if not rows:
        return "(no rows)"
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    header = " | ".join(c.ljust(widths[c]) for c in columns)
    sep = "-+-".join("-" * widths[c] for c in columns)
    body = "\n".join(" | ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns) for r in rows)
    return f"{header}\n{sep}\n{body}"


# ---------------------------------------------------------------------------
# Misc validation helpers
# ---------------------------------------------------------------------------
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_identifier(name: str) -> str:
    """Validate a SQL identifier (catalog/schema/table) to prevent injection.

    Raises ``ValueError`` if ``name`` is not a simple identifier. Use this on
    any widget-sourced value that will be interpolated into DDL.
    """
    name = name.strip()
    if not _IDENT_RE.match(name):
        raise ValueError(
            f"Unsafe SQL identifier: {name!r}. Use letters, digits, underscore; "
            "must not start with a digit."
        )
    return name
