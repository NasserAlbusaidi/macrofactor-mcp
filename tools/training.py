"""
MacroFactor MCP Server — Training Tools

Tools for weight trends, workout logs, workout history, muscle groups,
exercise progress, and exercise listing.
"""

from datetime import date, timedelta

from main import mcp, get_db


@mcp.tool()
def get_weight_trend(start_date: str = "", end_date: str = "") -> str:
    """Get weight trend data. Defaults to last 30 days.

    Args:
        start_date: Start date (YYYY-MM-DD). Default: 30 days ago.
        end_date: End date (YYYY-MM-DD). Default: today.
    """
    if not end_date:
        end_date = date.today().strftime("%Y-%m-%d")
    if not start_date:
        start_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")

    with get_db(read_only=True) as db:
        rows = db.execute(
            """SELECT date, weight_kg, trend_weight_kg, expenditure, fat_percent
               FROM daily
               WHERE date BETWEEN ? AND ?
                 AND (weight_kg IS NOT NULL OR trend_weight_kg IS NOT NULL)
               ORDER BY date""",
            [start_date, end_date],
        ).fetchall()

    if not rows:
        return f"No weight data between {start_date} and {end_date}."

    lines = [f"Weight Trend: {start_date} to {end_date}\n"]
    lines.append(f"{'Date':<12} {'Scale':>7} {'Trend':>7} {'BF%':>5} {'Expenditure':>12}")
    lines.append("-" * 48)

    weights = [r[1] for r in rows if r[1] is not None]
    trends = [r[2] for r in rows if r[2] is not None]

    for d, wt, twt, exp, bf in rows:
        lines.append(
            f"{str(d):<12} {f'{wt:.2f}' if wt else '   -':>7} "
            f"{f'{twt:.2f}' if twt else '   -':>7} "
            f"{f'{bf:.1f}' if bf else '  -':>5} "
            f"{f'{exp:.0f}' if exp else '   -':>12}"
        )

    if len(weights) >= 2:
        lines.append("")
        lines.append(f"Scale range: {min(weights):.2f} - {max(weights):.2f} kg")
        lines.append(f"Change: {weights[-1] - weights[0]:+.2f} kg")
    if len(trends) >= 2:
        lines.append(f"Trend change: {trends[-1] - trends[0]:+.2f} kg")

    return "\n".join(lines)


@mcp.tool()
def get_workout_log(date_str: str = "") -> str:
    """Get workout details for a specific date.

    Args:
        date_str: Date (YYYY-MM-DD). Default: most recent workout.
    """
    with get_db(read_only=True) as db:
        if date_str:
            rows = db.execute(
                """SELECT workout_name, exercise, set_type, weight_kg,
                          reps, rir, duration_sec
                   FROM workouts WHERE date = ? ORDER BY rowid""",
                [date_str],
            ).fetchall()
            title_date = date_str
        else:
            latest = db.execute("SELECT max(date) FROM workouts").fetchone()[0]
            if not latest:
                return "No workout data available. (Requires Quick Export format.)"
            rows = db.execute(
                """SELECT workout_name, exercise, set_type, weight_kg,
                          reps, rir, duration_sec
                   FROM workouts WHERE date = ? ORDER BY rowid""",
                [str(latest)],
            ).fetchall()
            title_date = str(latest)

    if not rows:
        return f"No workout data for {title_date}."

    workout_name = rows[0][0] or "Workout"
    dur_min = (rows[0][6] or 0) // 60
    lines = [f"{workout_name} — {title_date} ({dur_min} min)\n"]

    current_exercise = None
    for _, exercise, set_type, wt, reps, rir, _ in rows:
        if exercise != current_exercise:
            current_exercise = exercise
            lines.append(f"\n  {exercise}")
        wt_str = f"{wt:.1f}kg" if wt else "-"
        reps_str = f"{reps} reps" if reps else "-"
        rir_str = f"RIR {rir:.0f}" if rir is not None else ""
        set_label = f"[{set_type}]" if set_type else ""
        lines.append(f"    {set_label:<16} {wt_str:<10} {reps_str:<10} {rir_str}")

    return "\n".join(lines)


@mcp.tool()
def get_workout_history(start_date: str = "", end_date: str = "") -> str:
    """Get a summary of all workouts in a date range. Defaults to last 30 days.

    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
    """
    if not end_date:
        end_date = date.today().strftime("%Y-%m-%d")
    if not start_date:
        start_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")

    with get_db(read_only=True) as db:
        rows = db.execute(
            """SELECT date, workout_name, duration_sec,
                      count(*) as total_sets,
                      count(CASE WHEN set_type NOT IN ('Warm-Up Set') THEN 1 END) as work_sets,
                      sum(CASE WHEN weight_kg IS NOT NULL AND reps IS NOT NULL
                          THEN weight_kg * reps ELSE 0 END) as total_volume
               FROM workouts WHERE date BETWEEN ? AND ?
               GROUP BY date, workout_name, duration_sec ORDER BY date""",
            [start_date, end_date],
        ).fetchall()

    if not rows:
        return f"No workouts between {start_date} and {end_date}."

    lines = [f"Workout History: {start_date} to {end_date} ({len(rows)} sessions)\n"]
    for d, name, dur, total, work, vol in rows:
        dur_min = (dur or 0) // 60
        lines.append(f"  {d}  {name or 'Workout'}")
        lines.append(f"    {dur_min} min | {work} work sets | {vol:,.0f} kg total volume")

    return "\n".join(lines)


@mcp.tool()
def get_muscle_group_summary(start_date: str = "", end_date: str = "") -> str:
    """Get muscle group sets and volume summary. Defaults to last 7 days.

    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
    """
    if not end_date:
        end_date = date.today().strftime("%Y-%m-%d")
    if not start_date:
        start_date = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")

    with get_db(read_only=True) as db:
        sets_rows = db.execute(
            """SELECT muscle_group, sum(sets) as total_sets
               FROM muscle_sets WHERE date BETWEEN ? AND ?
               GROUP BY muscle_group ORDER BY total_sets DESC""",
            [start_date, end_date],
        ).fetchall()

        vol_rows = db.execute(
            """SELECT muscle_group, sum(volume_kg) as total_vol
               FROM muscle_volume WHERE date BETWEEN ? AND ?
               GROUP BY muscle_group ORDER BY total_vol DESC""",
            [start_date, end_date],
        ).fetchall()

    if not sets_rows and not vol_rows:
        return f"No muscle group data between {start_date} and {end_date}."

    vol_map = {mg: v for mg, v in vol_rows}
    lines = [f"Muscle Groups: {start_date} to {end_date}\n"]
    lines.append(f"{'Muscle Group':<20} {'Sets':>6} {'Volume (kg)':>12}")
    lines.append("-" * 42)
    for mg, sets in sets_rows:
        vol = vol_map.get(mg, 0)
        lines.append(f"{mg:<20} {sets:>6.1f} {vol:>12,.0f}")

    return "\n".join(lines)


@mcp.tool()
def get_exercise_progress(
    exercise: str, metric: str = "total_volume", limit: int = 20
) -> str:
    """Track progress for a specific exercise over time.

    Args:
        exercise: Exercise name (case-insensitive, partial match).
        metric: One of: estimated_1rm, estimated_3rm, estimated_10rm,
                total_volume, best_set_volume, heaviest_weight,
                total_reps, best_set_reps, total_sets,
                total_duration, best_set_duration,
                total_distance, best_set_distance.
        limit: Max data points to return (default 20, most recent).
    """
    with get_db(read_only=True) as db:
        rows = db.execute(
            """SELECT date, exercise, value
               FROM exercise_tracking
               WHERE lower(exercise) LIKE ? AND metric = ?
               ORDER BY date DESC LIMIT ?""",
            [f"%{exercise.lower()}%", metric, limit],
        ).fetchall()

        if not rows:
            exercises = db.execute(
                """SELECT DISTINCT exercise FROM exercise_tracking
                   WHERE lower(exercise) LIKE ? ORDER BY exercise LIMIT 10""",
                [f"%{exercise.lower()}%"],
            ).fetchall()
            if exercises:
                names = [e[0] for e in exercises]
                return f"No '{metric}' data for '{exercise}'. Similar: {', '.join(names)}"
            return f"No exercise matching '{exercise}' found."

    rows = list(reversed(rows))
    actual_exercise = rows[0][1]
    lines = [f"{actual_exercise} — {metric.replace('_', ' ').title()}\n"]
    lines.append(f"{'Date':<12} {'Value':>10}")
    lines.append("-" * 24)

    values = [r[2] for r in rows]
    for d, _, v in rows:
        lines.append(f"{str(d):<12} {v:>10.1f}")

    if len(values) >= 2:
        lines.append("")
        lines.append(f"First: {values[0]:.1f}  ->  Latest: {values[-1]:.1f}")
        change = values[-1] - values[0]
        pct = (change / values[0] * 100) if values[0] != 0 else 0
        lines.append(f"Change: {change:+.1f} ({pct:+.1f}%)")

    return "\n".join(lines)


@mcp.tool()
def list_exercises() -> str:
    """List all tracked exercises with available metrics."""
    with get_db(read_only=True) as db:
        rows = db.execute(
            """SELECT exercise, metric, count(*) as sessions,
                      min(date) as first, max(date) as last
               FROM exercise_tracking
               GROUP BY exercise, metric ORDER BY exercise, metric"""
        ).fetchall()

    if not rows:
        return "No exercise tracking data available."

    lines = ["Tracked Exercises\n"]
    current = None
    for ex, metric, cnt, first, last in rows:
        if ex != current:
            current = ex
            lines.append(f"\n  {ex}")
        lines.append(f"    {metric:<25} {cnt:>3} sessions  ({first} to {last})")

    return "\n".join(lines)
