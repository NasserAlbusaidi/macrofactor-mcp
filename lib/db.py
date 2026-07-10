"""
Database connection management and configuration.

Independent of MCP — can be imported by the library layer, local reports, or
anything else that needs database access.
"""

import os
import threading
import logging
from contextlib import contextmanager

import duckdb

logger = logging.getLogger("macrofactor-mcp")

# ── Config ───────────────────────────────────────────────────────────────────


def _configured_path(name: str, default: str) -> str:
    """Return an expanded absolute path from an environment variable."""
    value = os.path.expanduser(os.environ.get(name, default))
    return value if value == ":memory:" else os.path.abspath(value)


DATA_DIR = _configured_path("MACROFACTOR_DATA_DIR", "~/Downloads")
DB_PATH = _configured_path("MACROFACTOR_DB_PATH", "~/.macrofactor/macrofactor.duckdb")
GARMIN_TOKENSTORE = _configured_path("GARMINTOKENS", "~/.garminconnect")
PERSISTENT_DB = os.environ.get("MACROFACTOR_PERSISTENT_DB", "").lower() in {
    "1", "true", "yes", "on",
}

# ── DuckDB ───────────────────────────────────────────────────────────────────

if DB_PATH != ":memory:":
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

_db_conn = None
_db_lock = threading.Lock()


def _connect_with_retry(read_only=False, max_attempts=10, delay=0.5):
    """Connect to DuckDB with retries for lock conflicts."""
    import time

    for attempt in range(max_attempts):
        try:
            return duckdb.connect(DB_PATH, read_only=read_only)
        except duckdb.IOException as e:
            if "lock" in str(e).lower() and attempt < max_attempts - 1:
                logger.warning(
                    "DB locked (attempt %d/%d), retrying in %ss...",
                    attempt + 1, max_attempts, delay,
                )
                time.sleep(delay)
            else:
                raise


def _get_persistent_conn():
    """Get or create the persistent read/write DuckDB connection."""
    global _db_conn
    if _db_conn is not None:
        try:
            _db_conn.execute("SELECT 1")
            return _db_conn
        except Exception:
            logger.warning("Persistent DB connection stale, reconnecting...")
            try:
                _db_conn.close()
            except Exception:
                pass
            _db_conn = None

    _db_conn = _connect_with_retry(read_only=False)
    return _db_conn


@contextmanager
def get_db(read_only=False):
    """Get a DuckDB connection.

    MCP tools use short-lived read/write connections protected by a lock.
    Read-only reporting workflows use short-lived read-only connections so
    multiple reporting commands can run at the same time without taking the
    writer lock. Set MACROFACTOR_PERSISTENT_DB=1 to opt back into one persistent
    read/write connection for a single-process deployment.
    """
    if read_only:
        conn = _connect_with_retry(read_only=True)
        try:
            yield conn
        finally:
            conn.close()
        return

    with _db_lock:
        conn = _get_persistent_conn() if PERSISTENT_DB else _connect_with_retry(read_only=False)
        try:
            yield conn
        except Exception:
            if PERSISTENT_DB:
                global _db_conn
                try:
                    conn.close()
                except Exception:
                    pass
                _db_conn = None
            raise
        finally:
            if not PERSISTENT_DB:
                conn.close()


def close_db():
    """Close the persistent read/write connection if one is open."""
    global _db_conn
    with _db_lock:
        if _db_conn is not None:
            try:
                _db_conn.close()
            finally:
                _db_conn = None
