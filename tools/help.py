"""Setup and usage guidance for the MacroFactor MCP server."""

import glob
import os
from datetime import datetime

from main import mcp, get_db, DATA_DIR, DB_PATH, GARMIN_TOKENSTORE


def _yes_no(value: bool) -> str:
    return "OK" if value else "MISSING"


def _fmt_path(path: str) -> str:
    return os.path.expanduser(path)


def _latest_export() -> tuple[int, str | None]:
    pattern = os.path.join(DATA_DIR, "*.xlsx")
    matches = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    return len(matches), matches[0] if matches else None


def _db_status() -> dict:
    status = {
        "exists": os.path.exists(DB_PATH),
        "daily_rows": 0,
        "food_rows": 0,
        "workout_rows": 0,
        "garmin_rows": 0,
        "date_range": None,
        "garmin_range": None,
        "last_import": None,
        "last_sync": None,
        "error": None,
    }
    if not status["exists"]:
        return status

    try:
        with get_db(read_only=True) as db:
            status["daily_rows"] = db.execute("SELECT count(*) FROM daily").fetchone()[0]
            status["food_rows"] = db.execute("SELECT count(*) FROM food_log").fetchone()[0]
            status["workout_rows"] = db.execute("SELECT count(*) FROM workouts").fetchone()[0]
            status["date_range"] = db.execute("SELECT min(date), max(date) FROM daily").fetchone()
            status["garmin_rows"] = db.execute(
                "SELECT "
                "(SELECT count(*) FROM garmin_daily_stats) + "
                "(SELECT count(*) FROM garmin_activities) + "
                "(SELECT count(*) FROM garmin_sleep) + "
                "(SELECT count(*) FROM garmin_training_status) + "
                "(SELECT count(*) FROM garmin_body_fat)"
            ).fetchone()[0]
            status["garmin_range"] = db.execute(
                "SELECT min(date), max(date) FROM garmin_daily_stats"
            ).fetchone()
            status["last_import"] = db.execute(
                "SELECT filename, format, imported_at FROM import_log "
                "ORDER BY imported_at DESC LIMIT 1"
            ).fetchone()
            status["last_sync"] = db.execute(
                "SELECT synced_at, last_date_synced FROM garmin_sync_log "
                "ORDER BY synced_at DESC LIMIT 1"
            ).fetchone()
    except Exception as e:
        status["error"] = str(e)
    return status


@mcp.tool()
def setup_check(show_workflows: bool = True) -> str:
    """Check local setup and explain the easiest next action.

    Use this first when the MCP seems empty, stale, misconfigured, or when you
    are not sure which tool to call next.

    Args:
        show_workflows: Include common question-to-tool workflows.
    """
    export_count, latest = _latest_export()
    db = _db_status()
    garmin_tokens = os.path.isdir(_fmt_path(GARMIN_TOKENSTORE))

    lines = ["MacroFactor MCP Setup Check", "=" * 32]
    lines.append("")
    lines.append("CONFIG")
    lines.append(f"  Data dir:       {_yes_no(os.path.isdir(DATA_DIR))}  {DATA_DIR}")
    lines.append(f"  DuckDB path:    {_yes_no(db['exists'])}  {DB_PATH}")
    lines.append(f"  Garmin tokens:  {_yes_no(garmin_tokens)}  {GARMIN_TOKENSTORE}")

    lines.append("")
    lines.append("EXPORTS")
    lines.append(f"  .xlsx files found: {export_count}")
    if latest:
        mtime = datetime.fromtimestamp(os.path.getmtime(latest)).strftime("%Y-%m-%d %H:%M")
        lines.append(f"  Newest export: {os.path.basename(latest)} ({mtime})")
    else:
        lines.append("  No MacroFactor exports found. Drop an .xlsx export in the data dir.")

    lines.append("")
    lines.append("LOADED DATA")
    if db["error"]:
        lines.append(f"  Database error: {db['error']}")
    elif not db["exists"]:
        lines.append("  No database yet. Run import_export after adding an export.")
    else:
        date_range = db["date_range"]
        if date_range and date_range[0]:
            lines.append(f"  MacroFactor range: {date_range[0]} to {date_range[1]}")
        lines.append(f"  Daily rows: {db['daily_rows']}")
        lines.append(f"  Food rows: {db['food_rows']}")
        lines.append(f"  Workout rows: {db['workout_rows']}")
        lines.append(f"  Garmin rows: {db['garmin_rows']}")
        garmin_range = db["garmin_range"]
        if garmin_range and garmin_range[0]:
            lines.append(f"  Garmin range: {garmin_range[0]} to {garmin_range[1]}")
        if db["last_import"]:
            fn, fmt, ts = db["last_import"]
            lines.append(f"  Last import: {os.path.basename(fn)} ({fmt}) at {ts}")
        if db["last_sync"]:
            ts, last_date = db["last_sync"]
            lines.append(f"  Last Garmin sync: {ts} (through {last_date})")

    next_steps = []
    db_locked = bool(db["error"] and "could not set lock" in db["error"].lower())
    if db_locked:
        next_steps.append("A running process holds the DuckDB lock. Restart the MCP server after updating, or stop the old main.py process.")
    elif db["error"]:
        next_steps.append("Fix the database error above, then rerun setup_check.")
    elif export_count and db["daily_rows"] == 0:
        next_steps.append("Run import_export to load the newest MacroFactor export.")

    if db["error"]:
        pass
    elif not garmin_tokens:
        next_steps.append("Authenticate Garmin first, then run sync_garmin.")
    elif db["garmin_rows"] == 0:
        next_steps.append("Run sync_garmin to pull Garmin health data.")
    if db["daily_rows"] > 0:
        next_steps.append("Use weekly_report for a 7-day review.")

    lines.append("")
    lines.append("NEXT STEPS")
    for step in next_steps or ["Setup looks ready. Start with daily_briefing."]:
        lines.append(f"  - {step}")

    if show_workflows:
        lines.append("")
        lines.append("FLAGSHIP WORKFLOWS")
        lines.append("  - 'Am I recovered?' -> recovery_check")
        lines.append("  - 'What happened today?' -> daily_briefing")
        lines.append("  - 'What should change next week?' -> weekly_report")
        lines.append("  - 'Did my fueling work?' -> nutrition_performance_correlation")

    return "\n".join(lines)
