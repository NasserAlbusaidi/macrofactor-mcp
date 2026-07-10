"""Local fueling and recovery decision engine for MCP clients.

Combines MacroFactor nutrition, weight, and workout exports with optional
Garmin health and training data. Supports MacroFactor Quick Export and
All-Time Data workbooks.
"""

import os
import sys
import logging

# Ensure project directory is on sys.path so relative imports work
# regardless of the working directory when launched by MCP client
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Fix dual-import: when run as __main__, tool modules doing
# "from main import mcp" would create a second module instance
# with its own mcp object. This ensures they get the same one.
if __name__ == "__main__":
    sys.modules["main"] = sys.modules[__name__]

from mcp.server.fastmcp import FastMCP
from schemas import init_db

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("macrofactor-mcp")

# ── DB + Config (imported from lib layer, re-exported for tool modules) ──────
from lib.db import get_db, DATA_DIR, DB_PATH, GARMIN_TOKENSTORE  # noqa: F401


# ── FastMCP ──────────────────────────────────────────────────────────────────
mcp = FastMCP(
    "macrofactor",
    instructions=(
        "Local fueling and recovery decisions from MacroFactor data with optional Garmin data. "
        "Use setup_check first when configuration, data freshness, or next steps are unclear. "
        "MacroFactor exports are auto-imported on startup. "
        "Garmin data can be synced with the sync_garmin tool. "
        "Use recovery_check to assess today's training readiness. "
        "Use daily_briefing for a complete day view. "
        "Use weekly_report to decide what to change next week. "
        "Use nutrition_performance_correlation to test whether fueling supported recovery. "
        "Data persists in DuckDB — no need to reimport each session."
    ),
)


# ═════════════════════════════════════════════════════════════════════════════
#  DEFERRED IMPORTS (avoid circular imports — main.py objects are defined above)
# ═════════════════════════════════════════════════════════════════════════════

from importers import auto_import_new_exports

# ── Tool modules (import triggers @mcp.tool() registration) ────────────────
# Each module is imported individually so a failure in one doesn't block the rest.
_tool_modules = [
    "tools.help",
    "tools.data",
    "tools.nutrition",
    "tools.training",
    "tools.garmin",
    "tools.analysis",
    "tools.insights",
]

import importlib

for _mod_name in _tool_modules:
    try:
        importlib.import_module(_mod_name)
    except Exception as _e:
        logger.error("Failed to load tool module '%s': %s", _mod_name, _e, exc_info=True)

# ═════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Initialize local data and run the MCP server over stdio."""
    try:
        with get_db() as db:
            init_db(db)
        auto_import_new_exports()
    except Exception as e:
        logger.warning("Startup init/import skipped (DB likely locked by another session): %s", e)
        logger.warning("Server will start anyway — tools will connect on demand.")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
