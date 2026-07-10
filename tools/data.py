"""
MacroFactor MCP Server — Data Management Tools

Tools for importing, clearing, checking status, and querying raw data.
"""

import os
import glob

from main import mcp, get_db, DATA_DIR
from importers import load_xlsx


@mcp.tool()
def import_export(filename: str = "") -> str:
    """Import a MacroFactor .xlsx export file into the database.

    Supports both Quick Export and All-Time Data formats.
    If filename is empty, auto-detects the most recent .xlsx in the data directory.

    Args:
        filename: Path to .xlsx file, or empty to auto-detect.
    """
    if not filename:
        pattern = os.path.join(DATA_DIR, "*.xlsx")
        matches = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        if not matches:
            return f"No .xlsx export files found in {DATA_DIR}."
        filename = matches[0]

    if not os.path.exists(filename):
        return f"File not found: {filename}"

    with get_db() as db:
        existing = db.execute(
            "SELECT filename FROM import_log WHERE filename = ?", [filename]
        ).fetchone()
    if existing:
        return f"Already imported: {filename}. Call `clear_data` first to reimport."

    stats = load_xlsx(filename)
    fmt = stats.get("format", "unknown")

    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO import_log "
            "(filename, format, rows_daily, rows_food, rows_workouts) VALUES (?,?,?,?,?)",
            [filename, fmt, stats["daily"], stats["food"], stats["workouts"]],
        )

    return (
        f"Imported {os.path.basename(filename)} ({fmt} format):\n"
        f"  {stats['daily']} daily, {stats['food']} food, {stats['workouts']} workout rows"
    )


@mcp.tool()
def clear_data() -> str:
    """Clear all imported data so you can reimport fresh exports."""
    with get_db() as db:
        for table in [
            "daily", "food_log", "workouts", "muscle_sets", "muscle_volume",
            "nutrition_targets", "exercise_tracking", "body_metrics",
            "custom_foods", "food_log_notes", "import_log",
        ]:
            db.execute(f"DELETE FROM {table}")
    return "All data cleared. Use `import_export` to reload."


@mcp.tool()
def data_status() -> str:
    """Check what data is currently loaded — date ranges, row counts, last import."""
    with get_db(read_only=True) as db:
        rows = {}
        for t in ["daily", "food_log", "workouts", "muscle_sets", "muscle_volume",
                   "exercise_tracking", "body_metrics", "custom_foods",
                   "nutrition_targets", "food_log_notes"]:
            try:
                rows[t] = db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            except Exception:
                rows[t] = 0

        date_range = db.execute("SELECT min(date), max(date) FROM daily").fetchone()
        imports = db.execute(
            "SELECT filename, format, imported_at "
            "FROM import_log ORDER BY imported_at DESC LIMIT 5"
        ).fetchall()

        # Garmin data
        garmin_tables = [
            "garmin_daily_stats", "garmin_activities", "garmin_sleep",
            "garmin_training_status", "garmin_body_fat",
        ]
        garmin_rows = {}
        for t in garmin_tables:
            try:
                garmin_rows[t] = db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            except Exception:
                garmin_rows[t] = 0

        total_garmin = sum(garmin_rows.values())
        garmin_range = None
        last_sync = None
        if total_garmin > 0:
            garmin_range = db.execute(
                "SELECT min(date), max(date) FROM garmin_daily_stats"
            ).fetchone()
            last_sync = db.execute(
                "SELECT synced_at, last_date_synced FROM garmin_sync_log "
                "ORDER BY synced_at DESC LIMIT 1"
            ).fetchone()

    lines = ["MacroFactor Data Status\n"]
    if date_range[0]:
        lines.append(f"Date range: {date_range[0]} to {date_range[1]}")
    lines.append(f"Daily summaries: {rows['daily']}")
    lines.append(f"Food log entries: {rows['food_log']}")
    lines.append(f"Workout sets: {rows['workouts']}")
    lines.append(f"Muscle group records: {rows['muscle_sets']} (sets), {rows['muscle_volume']} (volume)")
    lines.append(f"Exercise tracking: {rows['exercise_tracking']}")
    lines.append(f"Body metrics: {rows['body_metrics']}")
    lines.append(f"Custom foods: {rows['custom_foods']}")
    lines.append(f"Nutrition targets: {rows['nutrition_targets']}")
    lines.append(f"Food log notes: {rows['food_log_notes']}")

    if imports:
        lines.append("\nRecent imports:")
        for fn, fmt, ts in imports:
            lines.append(f"  {os.path.basename(fn)} ({fmt}) at {ts}")

    if rows["daily"] == 0:
        lines.append("\nNo data loaded. Call `import_export` to load.")

    if total_garmin > 0:
        lines.append("\nGarmin Data:")
        if garmin_range and garmin_range[0]:
            lines.append(f"  Date range: {garmin_range[0]} to {garmin_range[1]}")
        lines.append(f"  Daily stats:      {garmin_rows['garmin_daily_stats']}")
        lines.append(f"  Activities:       {garmin_rows['garmin_activities']}")
        lines.append(f"  Sleep records:    {garmin_rows['garmin_sleep']}")
        lines.append(f"  Training status:  {garmin_rows['garmin_training_status']}")
        lines.append(f"  Body fat records: {garmin_rows['garmin_body_fat']}")
        if last_sync:
            lines.append(f"  Last sync: {last_sync[0]} (up to {last_sync[1]})")
    else:
        lines.append("\nNo Garmin data. Run `sync_garmin` to pull health data.")

    return "\n".join(lines)


@mcp.tool()
def query(sql: str) -> str:
    """Run a custom SQL query against the MacroFactor database.

    Available tables:
      - daily: date, calories, macros, targets, weight, expenditure, steps, micros
      - food_log: date, time, food details (from Quick Export)
      - workouts: date, exercise, sets, reps, weight (from Quick Export)
      - muscle_sets: date, muscle_group, sets
      - muscle_volume: date, muscle_group, volume_kg
      - exercise_tracking: date, exercise, metric, value (from All-Time)
      - body_metrics: date, metric, value
      - custom_foods: food_name, nutrition info
      - nutrition_targets: program_date, weekday, targets
      - food_log_notes: date, name, note
      - garmin_daily_stats: date, steps, calories, HR, stress, body battery
      - garmin_activities: activity_id, date, type, duration, distance, HR, power
      - garmin_sleep: date, sleep phases, score, HRV, SpO2
      - garmin_training_status: date, status, VO2max, training load, FTP
      - garmin_body_fat: date, body_fat_pct
      - garmin_sync_log: sync history

    Use `data_status` to see what's loaded.

    Args:
        sql: SQL query (read-only SELECT statements only).
    """
    sql_clean = sql.strip().rstrip(";")
    sql_stripped = sql_clean.lower()
    if ";" in sql_clean:
        return "Only one SELECT query is allowed."
    if not sql_stripped.startswith("select") and not sql_stripped.startswith("with"):
        return "Only SELECT queries are allowed."

    try:
        with get_db(read_only=True) as db:
            cursor = db.execute(sql_clean)
            cols = [d[0] for d in cursor.description]
            result = cursor.fetchmany(101)
            if not result:
                return "Query returned no results."

        lines = ["\t".join(cols)]
        shown = result[:100]
        for row in shown:
            lines.append("\t".join(str(v) if v is not None else "" for v in row))
        if len(result) > 100:
            lines.append("\n... (showing first 100 rows; add LIMIT/OFFSET to page)")

        return "\n".join(lines)
    except Exception as e:
        return f"Query error: {e}"
