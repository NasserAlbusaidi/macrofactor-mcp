"""
MacroFactor MCP Server — Analysis Tools

Tools for compliance reports, digests, phase detection, meal pattern analysis,
and cross-domain analysis (combined data, weekly reports, recovery, correlations,
sleep analysis, body composition trends).
"""

from datetime import datetime, date, timedelta

from main import mcp, get_db
from garmin_sync import _TRAINING_STATUS_LABELS


@mcp.tool()
def get_compliance_report(start_date: str, end_date: str) -> str:
    """Compare actual intake vs targets over a date range. Shows adherence.

    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
    """
    with get_db(read_only=True) as db:
        rows = db.execute(
            """SELECT date, calories_kcal, protein_g, fat_g, carbs_g,
                      target_calories, target_protein, target_fat, target_carbs,
                      expenditure
               FROM daily WHERE date BETWEEN ? AND ? ORDER BY date""",
            [start_date, end_date],
        ).fetchall()

    if not rows:
        return f"No data between {start_date} and {end_date}."

    n = len(rows)
    sum_cal = sum_pro = sum_fat = sum_carb = 0
    sum_tcal = sum_tpro = sum_tfat = sum_tcarb = 0
    sum_exp = 0
    cal_hit = pro_hit = days_with_data = days_with_targets = 0

    for d, cal, pro, fat, carb, tcal, tpro, tfat, tcarb, exp in rows:
        cal, pro, fat, carb = cal or 0, pro or 0, fat or 0, carb or 0
        tcal, tpro, tfat, tcarb, exp = tcal or 0, tpro or 0, tfat or 0, tcarb or 0, exp or 0
        if cal > 0: days_with_data += 1
        if tcal > 0: days_with_targets += 1
        sum_cal += cal; sum_pro += pro; sum_fat += fat; sum_carb += carb
        sum_tcal += tcal; sum_tpro += tpro; sum_tfat += tfat; sum_tcarb += tcarb
        sum_exp += exp
        if tcal > 0 and cal > 0 and abs(cal - tcal) / tcal <= 0.10: cal_hit += 1
        if tpro > 0 and pro >= tpro * 0.90: pro_hit += 1

    pct = lambda a, t: f"{a/t*100:.0f}%" if t > 0 else "N/A"
    avg_n = max(days_with_data, 1)
    avg_tn = max(days_with_targets, 1)

    lines = [
        f"Compliance Report: {start_date} to {end_date} ({n} days)\n",
        f"Days with intake data: {days_with_data}",
        f"Days with targets: {days_with_targets}\n",
        f"{'Metric':<18} {'Avg Actual':>11} {'Avg Target':>11} {'Adherence':>10}",
        "-" * 55,
        f"{'Calories (kcal)':<18} {sum_cal/avg_n:>11.0f} {sum_tcal/avg_tn:>11.0f} {pct(sum_cal, sum_tcal):>10}",
        f"{'Protein (g)':<18} {sum_pro/avg_n:>11.0f} {sum_tpro/avg_tn:>11.0f} {pct(sum_pro, sum_tpro):>10}",
        f"{'Fat (g)':<18} {sum_fat/avg_n:>11.0f} {sum_tfat/avg_tn:>11.0f} {pct(sum_fat, sum_tfat):>10}",
        f"{'Carbs (g)':<18} {sum_carb/avg_n:>11.0f} {sum_tcarb/avg_tn:>11.0f} {pct(sum_carb, sum_tcarb):>10}",
        "",
        f"Days within 10% of calorie target: {cal_hit}/{days_with_targets} ({cal_hit/max(days_with_targets,1)*100:.0f}%)",
        f"Days hitting 90%+ protein target: {pro_hit}/{days_with_targets} ({pro_hit/max(days_with_targets,1)*100:.0f}%)",
        "",
        f"Avg daily expenditure: {sum_exp/n:,.0f} kcal",
        f"Avg daily balance: {(sum_cal-sum_exp)/avg_n:+,.0f} kcal",
        f"Total period balance: {sum_cal-sum_exp:+,.0f} kcal",
    ]
    return "\n".join(lines)


@mcp.tool()
def get_digest(period: str = "week", end_date: str = "") -> str:
    """Get a comprehensive weekly or monthly digest combining nutrition, weight, training, and adherence.

    One call instead of five — gives a complete picture of a period.

    Args:
        period: "week" (last 7 days) or "month" (last 30 days).
        end_date: End date (YYYY-MM-DD). Default: today.
    """
    if not end_date:
        end_date = date.today().strftime("%Y-%m-%d")
    days = 7 if period == "week" else 30
    start_date = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")

    with get_db(read_only=True) as db:
        # Nutrition
        nutrition = db.execute(
            """SELECT count(*) as n,
                      avg(calories_kcal), avg(protein_g), avg(fat_g), avg(carbs_g),
                      avg(target_calories), avg(target_protein),
                      avg(expenditure),
                      sum(calories_kcal), sum(expenditure)
               FROM daily WHERE date BETWEEN ? AND ? AND calories_kcal > 0""",
            [start_date, end_date],
        ).fetchone()

        # Weight
        weight = db.execute(
            """SELECT min(trend_weight_kg), max(trend_weight_kg),
                      (SELECT trend_weight_kg FROM daily WHERE date >= ? AND trend_weight_kg IS NOT NULL ORDER BY date LIMIT 1),
                      (SELECT trend_weight_kg FROM daily WHERE date <= ? AND trend_weight_kg IS NOT NULL ORDER BY date DESC LIMIT 1),
                      avg(weight_kg), min(weight_kg), max(weight_kg)
               FROM daily WHERE date BETWEEN ? AND ?""",
            [start_date, end_date, start_date, end_date],
        ).fetchone()

        # Training
        training = db.execute(
            """SELECT count(DISTINCT date) as sessions,
                      sum(CASE WHEN set_type NOT IN ('Warm-Up Set') THEN 1 ELSE 0 END) as work_sets,
                      sum(CASE WHEN weight_kg IS NOT NULL AND reps IS NOT NULL
                          THEN weight_kg * reps ELSE 0 END) as total_volume
               FROM workouts WHERE date BETWEEN ? AND ?""",
            [start_date, end_date],
        ).fetchone()

        # Muscle groups top 5
        muscles = db.execute(
            """SELECT muscle_group, sum(sets) as total
               FROM muscle_sets WHERE date BETWEEN ? AND ?
               GROUP BY muscle_group ORDER BY total DESC LIMIT 5""",
            [start_date, end_date],
        ).fetchall()

        # Adherence
        adherence = db.execute(
            """SELECT count(CASE WHEN calories_kcal > 0 AND target_calories > 0
                          AND abs(calories_kcal - target_calories) / target_calories <= 0.10
                          THEN 1 END) as cal_hit,
                      count(CASE WHEN protein_g > 0 AND target_protein > 0
                          AND protein_g >= target_protein * 0.90
                          THEN 1 END) as pro_hit,
                      count(CASE WHEN target_calories > 0 THEN 1 END) as target_days
               FROM daily WHERE date BETWEEN ? AND ?""",
            [start_date, end_date],
        ).fetchone()

        # Steps
        steps = db.execute(
            """SELECT avg(steps), sum(steps)
               FROM daily WHERE date BETWEEN ? AND ? AND steps > 0""",
            [start_date, end_date],
        ).fetchone()

    label = "Weekly" if period == "week" else "Monthly"
    lines = [f"{label} Digest: {start_date} to {end_date}\n"]
    lines.append("=" * 50)

    # Nutrition section
    n = nutrition[0] or 1
    avg_cal, avg_pro, avg_fat, avg_carb = nutrition[1] or 0, nutrition[2] or 0, nutrition[3] or 0, nutrition[4] or 0
    avg_tcal, avg_tpro = nutrition[5] or 0, nutrition[6] or 0
    avg_exp = nutrition[7] or 0
    total_cal, total_exp = nutrition[8] or 0, nutrition[9] or 0

    lines.append(f"\nNUTRITION ({n} days logged)")
    lines.append(f"  Avg intake:     {avg_cal:,.0f} kcal  (P {avg_pro:.0f}g / F {avg_fat:.0f}g / C {avg_carb:.0f}g)")
    lines.append(f"  Avg target:     {avg_tcal:,.0f} kcal  (P {avg_tpro:.0f}g)")
    lines.append(f"  Avg expenditure:{avg_exp:>7,.0f} kcal")
    lines.append(f"  Avg balance:    {avg_cal - avg_exp:>+7,.0f} kcal/day")
    lines.append(f"  Period balance: {total_cal - total_exp:>+7,.0f} kcal total")

    # Weight section
    if weight[2] and weight[3]:
        trend_change = weight[3] - weight[2]
        lines.append(f"\nWEIGHT")
        lines.append(f"  Trend: {weight[2]:.2f} -> {weight[3]:.2f} kg ({trend_change:+.2f} kg)")
        if weight[5] and weight[6]:
            lines.append(f"  Scale range: {weight[5]:.2f} - {weight[6]:.2f} kg")
        expected_from_balance = (total_cal - total_exp) / 7700  # ~7700 kcal per kg
        lines.append(f"  Expected from energy balance: {expected_from_balance:+.2f} kg")

    # Training section
    sess, work_sets, vol = training[0] or 0, training[1] or 0, training[2] or 0
    if sess > 0:
        lines.append(f"\nTRAINING")
        lines.append(f"  Sessions: {sess}")
        lines.append(f"  Work sets: {work_sets}")
        lines.append(f"  Total volume: {vol:,.0f} kg")
        if muscles:
            top = ", ".join(f"{m[0]} ({m[1]:.0f})" for m in muscles[:5])
            lines.append(f"  Top muscles: {top}")

    # Adherence section
    target_days = adherence[2] or 1
    cal_hit, pro_hit = adherence[0] or 0, adherence[1] or 0
    lines.append(f"\nADHERENCE")
    lines.append(f"  Calories within 10%: {cal_hit}/{target_days} ({cal_hit/target_days*100:.0f}%)")
    lines.append(f"  Protein hitting 90%: {pro_hit}/{target_days} ({pro_hit/target_days*100:.0f}%)")

    # Steps section
    if steps[0]:
        lines.append(f"\nSTEPS")
        lines.append(f"  Daily avg: {steps[0]:,.0f}")
        lines.append(f"  Total: {steps[1]:,.0f}")

    return "\n".join(lines)


@mcp.tool()
def detect_phases(min_days: int = 14) -> str:
    """Auto-detect cut/bulk/maintenance phases from weight trend and energy balance.

    Analyzes the full history and identifies distinct phases based on trend weight
    direction and caloric balance. Each phase shows duration, weight change, and
    average deficit/surplus.

    Args:
        min_days: Minimum days for a phase to be reported (default 14).
    """
    with get_db(read_only=True) as db:
        rows = db.execute(
            """SELECT date, trend_weight_kg, calories_kcal, expenditure
               FROM daily
               WHERE trend_weight_kg IS NOT NULL
               ORDER BY date"""
        ).fetchall()

    if len(rows) < min_days:
        return f"Not enough data ({len(rows)} days). Need at least {min_days}."

    # Smooth trend into 14-day rolling windows
    window = 14
    phases = []
    current_phase = None

    for i in range(window, len(rows)):
        d, tw, cal, exp = rows[i]
        prev_tw = rows[i - window][1]

        if prev_tw is None or tw is None:
            continue

        rate_per_week = (tw - prev_tw) / window * 7  # kg/week

        if rate_per_week < -0.15:
            phase_type = "CUT"
        elif rate_per_week > 0.15:
            phase_type = "BULK"
        else:
            phase_type = "MAINTENANCE"

        if current_phase is None or current_phase["type"] != phase_type:
            if current_phase is not None:
                phases.append(current_phase)
            current_phase = {
                "type": phase_type,
                "start": str(d),
                "end": str(d),
                "start_weight": tw,
                "end_weight": tw,
                "days": 1,
                "total_cal": cal or 0,
                "total_exp": exp or 0,
                "cal_days": 1 if cal and cal > 0 else 0,
            }
        else:
            current_phase["end"] = str(d)
            current_phase["end_weight"] = tw
            current_phase["days"] += 1
            current_phase["total_cal"] += (cal or 0)
            current_phase["total_exp"] += (exp or 0)
            if cal and cal > 0:
                current_phase["cal_days"] += 1

    if current_phase:
        phases.append(current_phase)

    # Filter short phases and merge adjacent same-type phases
    merged = []
    for p in phases:
        if p["days"] < min_days:
            if merged and merged[-1]["type"] == p.get("prev_type", ""):
                continue  # skip short interruptions
            continue
        if merged and merged[-1]["type"] == p["type"]:
            # Merge into previous
            merged[-1]["end"] = p["end"]
            merged[-1]["end_weight"] = p["end_weight"]
            merged[-1]["days"] += p["days"]
            merged[-1]["total_cal"] += p["total_cal"]
            merged[-1]["total_exp"] += p["total_exp"]
            merged[-1]["cal_days"] += p["cal_days"]
        else:
            merged.append(p)

    if not merged:
        return "Could not detect distinct phases. Try a lower min_days value."

    lines = ["Detected Phases\n"]
    lines.append(f"{'Phase':<14} {'Period':<27} {'Days':>5} {'Weight Change':>14} {'Avg Balance':>12}")
    lines.append("-" * 76)

    for p in merged:
        wt_change = p["end_weight"] - p["start_weight"]
        avg_balance = 0
        if p["cal_days"] > 0:
            avg_balance = (p["total_cal"] - p["total_exp"]) / p["cal_days"]

        lines.append(
            f"{p['type']:<14} {p['start']} to {p['end']:<11} "
            f"{p['days']:>5} "
            f"{wt_change:>+10.2f} kg   "
            f"{avg_balance:>+8.0f} kcal"
        )

    # Summary
    total_days = sum(p["days"] for p in merged)
    cut_days = sum(p["days"] for p in merged if p["type"] == "CUT")
    bulk_days = sum(p["days"] for p in merged if p["type"] == "BULK")
    maint_days = sum(p["days"] for p in merged if p["type"] == "MAINTENANCE")

    lines.append("")
    lines.append(f"Total tracked: {total_days} days")
    lines.append(f"  Cut: {cut_days} days ({cut_days/total_days*100:.0f}%)")
    lines.append(f"  Bulk: {bulk_days} days ({bulk_days/total_days*100:.0f}%)")
    lines.append(f"  Maintenance: {maint_days} days ({maint_days/total_days*100:.0f}%)")

    # Overall
    first_w = merged[0]["start_weight"]
    last_w = merged[-1]["end_weight"]
    lines.append(f"\nOverall: {first_w:.2f} -> {last_w:.2f} kg ({last_w - first_w:+.2f} kg)")

    return "\n".join(lines)


@mcp.tool()
def analyze_meal_patterns(start_date: str = "", end_date: str = "", limit: int = 15) -> str:
    """Analyze meal timing, frequency, and which foods correlate with hitting targets.

    Shows when you eat, how many meals per day, most frequent foods, and which
    foods appear most on days where you hit your calorie and protein targets.

    Args:
        start_date: Start date (YYYY-MM-DD). Default: 30 days ago.
        end_date: End date (YYYY-MM-DD). Default: today.
        limit: Max foods to show per category (default 15).
    """
    if not end_date:
        end_date = date.today().strftime("%Y-%m-%d")
    if not start_date:
        start_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")

    with get_db(read_only=True) as db:
        # Meal timing
        timing = db.execute(
            """SELECT
                 CASE
                   WHEN cast(substr(time, 1, 2) as int) < 10 THEN 'Early (before 10am)'
                   WHEN cast(substr(time, 1, 2) as int) < 13 THEN 'Mid-morning (10am-1pm)'
                   WHEN cast(substr(time, 1, 2) as int) < 16 THEN 'Afternoon (1pm-4pm)'
                   WHEN cast(substr(time, 1, 2) as int) < 20 THEN 'Evening (4pm-8pm)'
                   ELSE 'Late night (after 8pm)'
                 END as time_window,
                 count(*) as entries,
                 sum(calories_kcal) as total_cal
               FROM food_log
               WHERE date BETWEEN ? AND ? AND time IS NOT NULL AND time != ''
               GROUP BY time_window ORDER BY min(cast(substr(time, 1, 2) as int))""",
            [start_date, end_date],
        ).fetchall()

        # Meals per day
        meals_per_day = db.execute(
            """SELECT date, count(*) as items
               FROM food_log WHERE date BETWEEN ? AND ?
               GROUP BY date""",
            [start_date, end_date],
        ).fetchall()

        # Most frequent foods
        frequent = db.execute(
            """SELECT food_name, count(*) as freq,
                      avg(calories_kcal) as avg_cal,
                      avg(protein_g) as avg_pro
               FROM food_log WHERE date BETWEEN ? AND ?
               GROUP BY food_name ORDER BY freq DESC LIMIT ?""",
            [start_date, end_date, limit],
        ).fetchall()

        # Foods on good adherence days (within 10% of calorie target AND 90%+ protein)
        good_day_foods = db.execute(
            """SELECT fl.food_name, count(*) as freq,
                      avg(fl.calories_kcal) as avg_cal,
                      avg(fl.protein_g) as avg_pro
               FROM food_log fl
               JOIN daily d ON fl.date = d.date
               WHERE fl.date BETWEEN ? AND ?
                 AND d.calories_kcal > 0 AND d.target_calories > 0
                 AND abs(d.calories_kcal - d.target_calories) / d.target_calories <= 0.10
                 AND d.protein_g >= d.target_protein * 0.90
               GROUP BY fl.food_name ORDER BY freq DESC LIMIT ?""",
            [start_date, end_date, limit],
        ).fetchall()

        # Foods on bad days (>15% over calorie target)
        bad_day_foods = db.execute(
            """SELECT fl.food_name, count(*) as freq,
                      avg(fl.calories_kcal) as avg_cal
               FROM food_log fl
               JOIN daily d ON fl.date = d.date
               WHERE fl.date BETWEEN ? AND ?
                 AND d.calories_kcal > 0 AND d.target_calories > 0
                 AND (d.calories_kcal - d.target_calories) / d.target_calories > 0.15
               GROUP BY fl.food_name ORDER BY freq DESC LIMIT ?""",
            [start_date, end_date, limit],
        ).fetchall()

    lines = [f"Meal Pattern Analysis: {start_date} to {end_date}\n"]
    lines.append("=" * 50)

    # Timing
    if timing:
        lines.append("\nMEAL TIMING")
        for time_window, entries, cal in timing:
            lines.append(f"  {time_window:<28} {entries:>4} items  ({cal:,.0f} kcal total)")

    # Meals per day
    if meals_per_day:
        counts = [m[1] for m in meals_per_day]
        avg_items = sum(counts) / len(counts)
        lines.append(f"\nMEALS PER DAY")
        lines.append(f"  Avg items logged: {avg_items:.1f}")
        lines.append(f"  Range: {min(counts)} - {max(counts)} items")
        lines.append(f"  Days with food log: {len(meals_per_day)}")

    # Frequent foods
    if frequent:
        lines.append(f"\nMOST FREQUENT FOODS")
        for name, freq, cal, pro in frequent:
            lines.append(f"  {freq:>3}x  {name:<35} {cal or 0:.0f} kcal  P {pro or 0:.0f}g")

    # Good day foods
    if good_day_foods:
        lines.append(f"\nFOODS ON HIGH-ADHERENCE DAYS")
        for name, freq, cal, pro in good_day_foods:
            lines.append(f"  {freq:>3}x  {name:<35} {cal or 0:.0f} kcal  P {pro or 0:.0f}g")

    # Bad day foods
    if bad_day_foods:
        lines.append(f"\nFOODS ON OVER-TARGET DAYS (>15% over)")
        for name, freq, cal in bad_day_foods:
            lines.append(f"  {freq:>3}x  {name:<35} {cal or 0:.0f} kcal")

    if not timing and not frequent:
        lines.append("\nNo food log data in this period. Meal patterns require Quick Export data.")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
#  CROSS-DOMAIN ANALYSIS TOOLS (Tasks 9-14)
# ═════════════════════════════════════════════════════════════════════════════


def _fmt_sleep_sec(sec):
    """Format seconds into Xh Ym string."""
    if not sec:
        return "-"
    return f"{sec // 3600}h {(sec % 3600) // 60}m"


def _parse_date_range(start_date, end_date, default_days):
    """Parse start/end dates with defaults. Returns (start_str, end_str)."""
    if not end_date:
        end_date = date.today().strftime("%Y-%m-%d")
    if not start_date:
        start_date = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=default_days)).strftime("%Y-%m-%d")
    return start_date, end_date


def _training_status_label(feedback):
    """Convert raw training status feedback to human-readable label."""
    if not feedback:
        return "-"
    fb_base = feedback.split("_")[0]
    return _TRAINING_STATUS_LABELS.get(fb_base, feedback)


def _acwr_interpretation(ratio):
    """Interpret Acute:Chronic Workload Ratio."""
    if ratio is None:
        return ""
    if ratio < 0.8:
        return "under-training"
    if ratio <= 1.3:
        return "optimal zone"
    if ratio <= 1.5:
        return "caution — high"
    return "danger — injury risk"


@mcp.tool()
def get_combined_data(
    start_date: str = "",
    end_date: str = "",
    domains: list[str] | None = None,
) -> str:
    """Unified cross-domain query — returns merged nutrition + health data by date.

    One call replaces multiple tool calls. Returns all requested domains in one payload.

    Args:
        start_date: Start date (YYYY-MM-DD). Default: 7 days ago.
        end_date: End date (YYYY-MM-DD). Default: today.
        domains: List of domains to include. Options: nutrition, weight, sleep,
                 training, activity, workouts. Default: all.
    """
    start_date, end_date = _parse_date_range(start_date, end_date, 7)
    all_domains = {"nutrition", "weight", "sleep", "training", "activity", "workouts"}
    selected = set(domains) & all_domains if domains else all_domains

    with get_db(read_only=True) as db:
        # Collect all dates in range
        date_rows = db.execute(
            "SELECT DISTINCT d FROM ("
            "  SELECT date AS d FROM daily WHERE date BETWEEN ? AND ? "
            "  UNION SELECT date AS d FROM garmin_daily_stats WHERE date BETWEEN ? AND ? "
            "  UNION SELECT date AS d FROM garmin_sleep WHERE date BETWEEN ? AND ? "
            ") ORDER BY d",
            [start_date, end_date] * 3,
        ).fetchall()

        if not date_rows:
            return f"No data between {start_date} and {end_date}."

        dates = [str(r[0]) for r in date_rows]

        # Pre-fetch all data keyed by date
        nutrition_data = {}
        if "nutrition" in selected:
            for row in db.execute(
                "SELECT date, calories_kcal, protein_g, fat_g, carbs_g, "
                "target_calories, target_protein, target_fat, target_carbs, expenditure "
                "FROM daily WHERE date BETWEEN ? AND ?", [start_date, end_date]
            ).fetchall():
                nutrition_data[str(row[0])] = row[1:]

        weight_data = {}
        if "weight" in selected:
            for row in db.execute(
                "SELECT d.date, d.weight_kg, d.trend_weight_kg, d.fat_percent, g.body_fat_pct "
                "FROM daily d LEFT JOIN garmin_body_fat g ON d.date = g.date "
                "WHERE d.date BETWEEN ? AND ?", [start_date, end_date]
            ).fetchall():
                weight_data[str(row[0])] = row[1:]

        sleep_data = {}
        if "sleep" in selected:
            for row in db.execute(
                "SELECT date, total_sleep_sec, sleep_score, sleep_score_qualifier, "
                "deep_sleep_sec, rem_sleep_sec, avg_overnight_hrv "
                "FROM garmin_sleep WHERE date BETWEEN ? AND ?", [start_date, end_date]
            ).fetchall():
                sleep_data[str(row[0])] = row[1:]

        training_data = {}
        if "training" in selected:
            for row in db.execute(
                "SELECT date, training_status_feedback, acute_load, chronic_load, "
                "load_ratio, vo2max_running, vo2max_cycling, ftp_watts, ftp_sport "
                "FROM garmin_training_status WHERE date BETWEEN ? AND ?",
                [start_date, end_date]
            ).fetchall():
                training_data[str(row[0])] = row[1:]

        activity_data = {}
        if "activity" in selected:
            for row in db.execute(
                "SELECT date, total_steps, daily_step_goal, body_battery_high, "
                "body_battery_low, avg_stress, resting_hr, total_calories, active_calories "
                "FROM garmin_daily_stats WHERE date BETWEEN ? AND ?",
                [start_date, end_date]
            ).fetchall():
                activity_data[str(row[0])] = row[1:]

        workout_data = {}
        if "workouts" in selected:
            # MacroFactor workouts
            mf_workouts = db.execute(
                "SELECT date, workout_name, exercise, set_type, weight_kg, reps, rir "
                "FROM workouts WHERE date BETWEEN ? AND ? ORDER BY date, rowid",
                [start_date, end_date]
            ).fetchall()
            for row in mf_workouts:
                d = str(row[0])
                workout_data.setdefault(d, {"mf": [], "garmin": []})
                workout_data[d]["mf"].append(row[1:])

            # Garmin activities
            garmin_acts = db.execute(
                "SELECT date, activity_name, activity_type, duration_sec, avg_hr, "
                "calories, training_effect_aerobic "
                "FROM garmin_activities WHERE date BETWEEN ? AND ? ORDER BY date",
                [start_date, end_date]
            ).fetchall()
            for row in garmin_acts:
                d = str(row[0])
                workout_data.setdefault(d, {"mf": [], "garmin": []})
                workout_data[d]["garmin"].append(row[1:])

    lines = [f"Combined Data: {start_date} to {end_date}", "=" * 60]

    for d in dates:
        day_lines = []

        if "nutrition" in selected and d in nutrition_data:
            cal, pro, fat, carb, tcal, tpro, tfat, tcarb, exp = nutrition_data[d]
            cal, pro, fat, carb = cal or 0, pro or 0, fat or 0, carb or 0
            balance = (cal - (exp or 0)) if exp else None
            bal_str = f"  balance: {balance:+,.0f}" if balance is not None else ""
            day_lines.append(
                f"  Nutrition: {cal:,.0f} kcal (P {pro:.0f}g / F {fat:.0f}g / C {carb:.0f}g)"
                f"{bal_str}"
            )
            if tcal:
                day_lines.append(f"    Target: {tcal:,.0f} kcal (P {tpro:.0f}g)")

        if "weight" in selected and d in weight_data:
            wt, tw, bf_mf, bf_garmin = weight_data[d]
            parts = []
            if wt:
                parts.append(f"scale {wt:.1f} kg")
            if tw:
                parts.append(f"trend {tw:.1f} kg")
            bf = bf_garmin if bf_garmin else bf_mf
            if bf:
                parts.append(f"BF {bf:.1f}%")
            if parts:
                day_lines.append(f"  Weight: {' | '.join(parts)}")

        if "sleep" in selected and d in sleep_data:
            total, score, qual, deep, rem, hrv = sleep_data[d]
            dur_str = _fmt_sleep_sec(total)
            score_str = f"score {score} {qual or ''}" if score else ""
            hrv_str = f"HRV {hrv}" if hrv else ""
            parts = [p for p in [dur_str, score_str, hrv_str] if p and p != "-"]
            if parts:
                day_lines.append(f"  Sleep: {' | '.join(parts)}")

        if "training" in selected and d in training_data:
            fb, acute, chronic, ratio, vo2r, vo2c, ftp, ftp_s = training_data[d]
            status = _training_status_label(fb)
            parts = [f"status: {status}"]
            if ratio:
                parts.append(f"ACWR {ratio:.2f}")
            if vo2r:
                parts.append(f"VO2max {vo2r:.1f}")
            if ftp:
                parts.append(f"FTP {ftp}W")
            day_lines.append(f"  Training: {' | '.join(parts)}")

        if "activity" in selected and d in activity_data:
            steps, goal, bbh, bbl, stress, rhr, tcal, acal = activity_data[d]
            parts = []
            if steps:
                parts.append(f"{steps:,} steps")
            if bbh is not None and bbl is not None:
                parts.append(f"BB {bbl}-{bbh}")
            if stress:
                parts.append(f"stress {stress}")
            if rhr:
                parts.append(f"RHR {rhr}")
            if parts:
                day_lines.append(f"  Activity: {' | '.join(parts)}")

        if "workouts" in selected and d in workout_data:
            wd = workout_data[d]
            # Merge strength Garmin data with MF data
            strength_types = {"strength_training", "indoor_cardio"}
            garmin_strength = [g for g in wd["garmin"] if g[1] in strength_types]
            garmin_other = [g for g in wd["garmin"] if g[1] not in strength_types]

            if wd["mf"]:
                # Group MF exercises
                exercises = {}
                wk_name = wd["mf"][0][0] or "Workout"
                for _, ex, st, wkg, rp, rir in wd["mf"]:
                    exercises.setdefault(ex, []).append((st, wkg, rp, rir))
                ex_summary = []
                for ex, sets in exercises.items():
                    work = [s for s in sets if s[0] != "Warm-Up Set"]
                    ex_summary.append(f"{ex} ({len(work)} sets)")
                day_lines.append(f"  Workout: {wk_name} — {', '.join(ex_summary[:5])}")
                # Merge garmin strength info
                if garmin_strength:
                    gs = garmin_strength[0]
                    dur_min = int((gs[2] or 0) / 60)
                    parts = [f"{dur_min} min"]
                    if gs[3]:
                        parts.append(f"avg HR {gs[3]}")
                    if gs[5]:
                        parts.append(f"TE {gs[5]:.1f}")
                    day_lines.append(f"    Garmin: {', '.join(parts)}")

            # Non-strength Garmin activities
            for g in garmin_other:
                name, atype, dur, hr, cal, te = g
                dur_min = int((dur or 0) / 60)
                parts = [f"{dur_min} min"]
                if hr:
                    parts.append(f"avg HR {hr}")
                if cal:
                    parts.append(f"{cal} kcal")
                day_lines.append(f"  Activity: {name or atype} ({', '.join(parts)})")

        if day_lines:
            lines.append(f"\n{d}")
            lines.extend(day_lines)

    return "\n".join(lines)


@mcp.tool()
def weekly_report(end_date: str = "") -> str:
    """Comprehensive 7-day report — nutrition, workouts, sleep, body, activity in one call.

    Includes daily detail for weight and nutrition, full workout breakdown with
    merged MacroFactor + Garmin strength data, sleep trends, and training load.

    Args:
        end_date: Last day of the week (YYYY-MM-DD). Default: today.
    """
    if not end_date:
        end_date = date.today().strftime("%Y-%m-%d")
    start_date = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")

    with get_db(read_only=True) as db:
        # Nutrition daily
        nutrition = db.execute(
            "SELECT date, calories_kcal, protein_g, fat_g, carbs_g, "
            "target_calories, target_protein, expenditure "
            "FROM daily WHERE date BETWEEN ? AND ? ORDER BY date",
            [start_date, end_date]
        ).fetchall()

        # Body data
        body = db.execute(
            "SELECT d.date, d.weight_kg, d.trend_weight_kg, d.fat_percent, "
            "g.body_fat_pct, gs.body_battery_high, gs.body_battery_low "
            "FROM daily d "
            "LEFT JOIN garmin_body_fat g ON d.date = g.date "
            "LEFT JOIN garmin_daily_stats gs ON d.date = gs.date "
            "WHERE d.date BETWEEN ? AND ? ORDER BY d.date",
            [start_date, end_date]
        ).fetchall()

        # Workouts (MacroFactor)
        mf_workouts = db.execute(
            "SELECT date, workout_name, exercise, set_type, weight_kg, reps, rir, duration_sec "
            "FROM workouts WHERE date BETWEEN ? AND ? ORDER BY date, rowid",
            [start_date, end_date]
        ).fetchall()

        # Garmin activities
        garmin_acts = db.execute(
            "SELECT date, activity_name, activity_type, duration_sec, avg_hr, max_hr, "
            "calories, training_effect_aerobic, distance_m "
            "FROM garmin_activities WHERE date BETWEEN ? AND ? ORDER BY date",
            [start_date, end_date]
        ).fetchall()

        # Muscle volume
        muscles = db.execute(
            "SELECT muscle_group, sum(sets) as total_sets "
            "FROM muscle_sets WHERE date BETWEEN ? AND ? "
            "GROUP BY muscle_group ORDER BY total_sets DESC",
            [start_date, end_date]
        ).fetchall()

        # Training status (latest)
        training = db.execute(
            "SELECT date, training_status_feedback, acute_load, chronic_load, "
            "load_ratio, vo2max_running, vo2max_cycling, ftp_watts, ftp_sport "
            "FROM garmin_training_status WHERE date BETWEEN ? AND ? "
            "ORDER BY date DESC LIMIT 1",
            [start_date, end_date]
        ).fetchone()

        # Sleep
        sleep_rows = db.execute(
            "SELECT date, total_sleep_sec, sleep_score, sleep_score_qualifier, "
            "avg_overnight_hrv "
            "FROM garmin_sleep WHERE date BETWEEN ? AND ? ORDER BY date",
            [start_date, end_date]
        ).fetchall()

        # Activity stats
        activity = db.execute(
            "SELECT avg(total_steps), sum(active_calories), "
            "avg(resting_hr), avg(avg_stress) "
            "FROM garmin_daily_stats WHERE date BETWEEN ? AND ?",
            [start_date, end_date]
        ).fetchone()

    lines = [f"Weekly Report: {start_date} to {end_date}", "=" * 70]

    # ── NUTRITION ──
    lines.append("\nNUTRITION")
    if nutrition:
        lines.append(
            f"  {'Date':<12} {'Cal':>6} {'Target':>7} {'Pro':>5} {'Fat':>5} "
            f"{'Carb':>5} {'Balance':>8}"
        )
        lines.append("  " + "-" * 55)
        sum_cal = sum_pro = sum_fat = sum_carb = 0
        days_data = 0
        cal_hits = pro_hits = target_days = 0
        for d, cal, pro, fat, carb, tcal, tpro, exp in nutrition:
            cal, pro, fat, carb = cal or 0, pro or 0, fat or 0, carb or 0
            tcal, tpro, exp = tcal or 0, tpro or 0, exp or 0
            bal = cal - exp if exp else 0
            lines.append(
                f"  {str(d):<12} {cal:>6.0f} {tcal:>7.0f} {pro:>5.0f} {fat:>5.0f} "
                f"{carb:>5.0f} {bal:>+8.0f}"
            )
            if cal > 0:
                sum_cal += cal
                sum_pro += pro
                sum_fat += fat
                sum_carb += carb
                days_data += 1
            if tcal > 0:
                target_days += 1
                if abs(cal - tcal) / tcal <= 0.10:
                    cal_hits += 1
                if tpro > 0 and pro >= tpro * 0.90:
                    pro_hits += 1

        if days_data > 0:
            lines.append(f"\n  Averages: {sum_cal / days_data:,.0f} kcal | "
                         f"P {sum_pro / days_data:.0f}g | F {sum_fat / days_data:.0f}g | "
                         f"C {sum_carb / days_data:.0f}g")
        if target_days > 0:
            lines.append(f"  Days on target (cal within 10%): {cal_hits}/{target_days}")
            lines.append(f"  Days hitting protein (90%+): {pro_hits}/{target_days}")
    else:
        lines.append("  No nutrition data this week.")

    # ── BODY ──
    lines.append("\nBODY")
    if body:
        bb_values = []
        for d, wt, tw, bf_mf, bf_g, bbh, bbl in body:
            parts = [f"{str(d):<12}"]
            if wt:
                parts.append(f"scale {wt:.1f} kg")
            if tw:
                parts.append(f"trend {tw:.1f} kg")
            bf = bf_g if bf_g else bf_mf
            if bf:
                parts.append(f"BF {bf:.1f}%")
            if bbh is not None:
                parts.append(f"BB {bbl}-{bbh}")
                bb_values.append((bbh + bbl) / 2)
            if len(parts) > 1:
                lines.append(f"  {' | '.join(parts)}")
        if bb_values:
            lines.append(f"  Avg body battery (midpoint): {sum(bb_values) / len(bb_values):.0f}")
    else:
        lines.append("  No body data this week.")

    # ── WORKOUTS ──
    lines.append("\nWORKOUTS")
    if mf_workouts or garmin_acts:
        # Group MF workouts by date
        mf_by_date = {}
        for d, wname, ex, st, wkg, reps, rir, dur in mf_workouts:
            d_str = str(d)
            mf_by_date.setdefault(d_str, {"name": wname, "dur": dur, "exercises": {}})
            mf_by_date[d_str]["exercises"].setdefault(ex, []).append((st, wkg, reps, rir))

        # Group Garmin activities by date
        garmin_by_date = {}
        for d, aname, atype, dur, avghr, maxhr, cal, te, dist in garmin_acts:
            d_str = str(d)
            garmin_by_date.setdefault(d_str, []).append({
                "name": aname, "type": atype, "dur": dur, "avghr": avghr,
                "maxhr": maxhr, "cal": cal, "te": te, "dist": dist
            })

        strength_types = {"strength_training", "indoor_cardio"}
        all_workout_dates = sorted(set(list(mf_by_date.keys()) + list(garmin_by_date.keys())))

        for d in all_workout_dates:
            mf = mf_by_date.get(d)
            garmin_list = garmin_by_date.get(d, [])
            garmin_strength = [g for g in garmin_list if g["type"] in strength_types]
            garmin_other = [g for g in garmin_list if g["type"] not in strength_types]

            if mf:
                dur_min = (mf["dur"] or 0) // 60
                lines.append(f"\n  {d} — {mf['name'] or 'Workout'} ({dur_min} min)")
                # Merge Garmin strength data
                if garmin_strength:
                    gs = garmin_strength[0]
                    parts = []
                    if gs["avghr"]:
                        parts.append(f"avg HR {gs['avghr']}")
                    if gs["maxhr"]:
                        parts.append(f"max HR {gs['maxhr']}")
                    if gs["cal"]:
                        parts.append(f"{gs['cal']} kcal")
                    if gs["te"]:
                        parts.append(f"TE {gs['te']:.1f}")
                    if parts:
                        lines.append(f"    Garmin: {', '.join(parts)}")

                for ex, sets in mf["exercises"].items():
                    work_sets = [s for s in sets if s[0] != "Warm-Up Set"]
                    if work_sets:
                        set_strs = []
                        for st, wkg, rp, rir in work_sets:
                            wt_str = f"{wkg:.1f}kg" if wkg else "-"
                            rp_str = f"x{rp}" if rp else ""
                            set_strs.append(f"{wt_str}{rp_str}")
                        lines.append(f"    {ex}: {', '.join(set_strs)}")

            # Non-strength Garmin activities
            for g in garmin_other:
                dur_min = int((g["dur"] or 0) / 60)
                parts = [f"{dur_min} min"]
                if g["dist"]:
                    parts.append(f"{g['dist'] / 1000:.1f} km")
                if g["avghr"]:
                    parts.append(f"avg HR {g['avghr']}")
                if g["cal"]:
                    parts.append(f"{g['cal']} kcal")
                if g["te"]:
                    parts.append(f"TE {g['te']:.1f}")
                lines.append(f"\n  {d} — {g['name'] or g['type']} ({', '.join(parts)})")

        # Muscle volume summary
        if muscles:
            lines.append(f"\n  Volume by muscle group:")
            for mg, sets in muscles:
                lines.append(f"    {mg:<20} {sets:.0f} sets")
    else:
        lines.append("  No workout data this week.")

    # ── TRAINING STATUS ──
    lines.append("\nTRAINING STATUS")
    if training:
        d, fb, acute, chronic, ratio, vo2r, vo2c, ftp, ftp_s = training
        status = _training_status_label(fb)
        lines.append(f"  Latest ({d}): {status}")
        if acute and chronic:
            interp = _acwr_interpretation(ratio)
            lines.append(f"  Load: {acute:.0f} acute / {chronic:.0f} chronic (ACWR {ratio:.2f} — {interp})")
        if vo2r:
            lines.append(f"  VO2max (running): {vo2r:.1f}")
        if vo2c:
            lines.append(f"  VO2max (cycling): {vo2c:.1f}")
        if ftp:
            lines.append(f"  FTP: {ftp}W ({ftp_s})")
    else:
        lines.append("  No training status data this week.")

    # ── SLEEP ──
    lines.append("\nSLEEP")
    if sleep_rows:
        durations = [r[1] for r in sleep_rows if r[1]]
        scores = [r[2] for r in sleep_rows if r[2]]
        hrvs = [r[4] for r in sleep_rows if r[4]]

        if durations:
            avg_dur = sum(durations) / len(durations)
            lines.append(f"  Avg duration: {_fmt_sleep_sec(int(avg_dur))}")
        if scores:
            avg_score = sum(scores) / len(scores)
            best = max(sleep_rows, key=lambda r: r[2] or 0)
            worst = min(sleep_rows, key=lambda r: r[2] or 999)
            lines.append(f"  Avg score: {avg_score:.0f}")
            lines.append(f"  Best night: {best[0]} (score {best[2]})")
            lines.append(f"  Worst night: {worst[0]} (score {worst[2]})")
        if hrvs:
            lines.append(f"  Avg HRV: {sum(hrvs) / len(hrvs):.0f}")
    else:
        lines.append("  No sleep data this week.")

    # ── ACTIVITY ──
    lines.append("\nACTIVITY")
    if activity and activity[0]:
        avg_steps, total_active, avg_rhr, avg_stress = activity
        if avg_steps:
            lines.append(f"  Avg steps: {avg_steps:,.0f}")
        if total_active:
            lines.append(f"  Total active calories: {total_active:,}")
        if avg_rhr:
            lines.append(f"  Avg resting HR: {avg_rhr:.0f} bpm")
        if avg_stress:
            lines.append(f"  Avg stress: {avg_stress:.0f}")
    else:
        lines.append("  No activity data this week.")

    return "\n".join(lines)


@mcp.tool()
def recovery_check(date_str: str = "") -> str:
    """Readiness assessment — should you train hard today?

    Combines last night's sleep, body battery, training load ratio,
    and yesterday's nutrition compliance into a single readiness view.

    Args:
        date_str: Date to check (YYYY-MM-DD). Default: today.
    """
    if not date_str:
        date_str = date.today().isoformat()
    yesterday = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    week_ago = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")

    with get_db(read_only=True) as db:
        # Last night's sleep
        sleep = db.execute(
            "SELECT total_sleep_sec, sleep_score, sleep_score_qualifier, "
            "deep_sleep_sec, rem_sleep_sec, avg_overnight_hrv, resting_hr "
            "FROM garmin_sleep WHERE date = ?", [date_str]
        ).fetchone()

        # 7-day sleep averages
        sleep_avg = db.execute(
            "SELECT avg(total_sleep_sec), avg(sleep_score), avg(avg_overnight_hrv) "
            "FROM garmin_sleep WHERE date BETWEEN ? AND ?",
            [week_ago, date_str]
        ).fetchone()

        # Body battery
        bb = db.execute(
            "SELECT body_battery_high, body_battery_low "
            "FROM garmin_daily_stats WHERE date = ?", [date_str]
        ).fetchone()

        # Training status
        training = db.execute(
            "SELECT training_status_feedback, acute_load, chronic_load, load_ratio "
            "FROM garmin_training_status WHERE date <= ? "
            "ORDER BY date DESC LIMIT 1", [date_str]
        ).fetchone()

        # Yesterday's nutrition
        yesterday_nutrition = db.execute(
            "SELECT calories_kcal, protein_g, target_calories, target_protein, expenditure "
            "FROM daily WHERE date = ?", [yesterday]
        ).fetchone()

    has_data = sleep or bb or training or yesterday_nutrition
    if not has_data:
        return (
            f"No recovery data for {date_str}. "
            "Sync Garmin data and/or import MacroFactor exports first."
        )

    lines = [f"Recovery Check: {date_str}", "=" * 50]
    signals = []

    # ── SLEEP ──
    lines.append("\nSLEEP")
    if sleep:
        total, score, qual, deep, rem, hrv, rhr = sleep
        dur_str = _fmt_sleep_sec(total)
        lines.append(f"  Duration: {dur_str}")
        if score:
            lines.append(f"  Score: {score} ({qual or ''})")
        if deep:
            lines.append(f"  Deep: {_fmt_sleep_sec(deep)} | REM: {_fmt_sleep_sec(rem)}")
        if hrv:
            lines.append(f"  HRV: {hrv}")
        if rhr:
            lines.append(f"  Resting HR: {rhr} bpm")

        # Comparison to 7-day avg
        if sleep_avg and sleep_avg[0]:
            avg_dur, avg_score, avg_hrv = sleep_avg
            if total and avg_dur:
                diff_min = (total - avg_dur) / 60
                lines.append(f"\n  vs 7-day avg: {diff_min:+.0f} min sleep")
                if total < avg_dur * 0.85:
                    signals.append("SLEEP: below average duration")
                elif total > avg_dur * 1.05:
                    signals.append("SLEEP: above average duration (+)")
            if score and avg_score:
                lines.append(f"  vs 7-day avg score: {score - avg_score:+.0f}")
                if score < avg_score - 10:
                    signals.append("SLEEP: score below average")
                elif score >= avg_score:
                    signals.append("SLEEP: score at or above average (+)")
            if hrv and avg_hrv:
                lines.append(f"  vs 7-day avg HRV: {hrv - avg_hrv:+.0f}")
                if hrv < avg_hrv * 0.85:
                    signals.append("HRV: significantly below average")
                elif hrv >= avg_hrv:
                    signals.append("HRV: at or above average (+)")
    else:
        lines.append("  No sleep data for this date.")

    # ── BODY BATTERY ──
    lines.append("\nBODY BATTERY")
    if bb:
        bbh, bbl = bb
        if bbh is not None:
            lines.append(f"  Range: {bbl} - {bbh}")
            if bbh >= 75:
                signals.append("BODY BATTERY: high start (+)")
            elif bbh < 40:
                signals.append("BODY BATTERY: low start — consider easy day")
    else:
        lines.append("  No body battery data.")

    # ── TRAINING LOAD ──
    lines.append("\nTRAINING LOAD")
    if training:
        fb, acute, chronic, ratio = training
        status = _training_status_label(fb)
        lines.append(f"  Status: {status}")
        if acute and chronic:
            interp = _acwr_interpretation(ratio)
            lines.append(f"  ACWR: {ratio:.2f} ({interp})")
            lines.append(f"  Acute: {acute:.0f} | Chronic: {chronic:.0f}")
            if ratio > 1.5:
                signals.append("LOAD: ACWR high — injury risk, consider rest")
            elif ratio > 1.3:
                signals.append("LOAD: ACWR elevated — train with caution")
            elif ratio < 0.8:
                signals.append("LOAD: ACWR low — room to push harder (+)")
            else:
                signals.append("LOAD: ACWR in optimal zone (+)")
    else:
        lines.append("  No training load data.")

    # ── YESTERDAY'S NUTRITION ──
    lines.append(f"\nYESTERDAY'S NUTRITION ({yesterday})")
    if yesterday_nutrition:
        cal, pro, tcal, tpro, exp = yesterday_nutrition
        cal, pro = cal or 0, pro or 0
        tcal, tpro, exp = tcal or 0, tpro or 0, exp or 0
        lines.append(f"  Intake: {cal:,.0f} kcal | Protein: {pro:.0f}g")
        if tcal > 0:
            cal_pct = cal / tcal * 100
            lines.append(f"  Cal adherence: {cal_pct:.0f}% of target ({tcal:,.0f})")
            if cal_pct < 85:
                signals.append("NUTRITION: significant deficit yesterday")
        if tpro > 0:
            pro_pct = pro / tpro * 100
            lines.append(f"  Protein adherence: {pro_pct:.0f}% of target ({tpro:.0f}g)")
            if pro_pct < 80:
                signals.append("NUTRITION: protein intake low yesterday")
            elif pro_pct >= 90:
                signals.append("NUTRITION: protein on target (+)")
        if exp > 0:
            balance = cal - exp
            lines.append(f"  Energy balance: {balance:+,.0f} kcal")
    else:
        lines.append("  No nutrition data for yesterday.")

    # ── SIGNALS ──
    lines.append("\nSIGNALS")
    if signals:
        positive = [s for s in signals if "(+)" in s]
        negative = [s for s in signals if "(+)" not in s]
        for s in positive:
            lines.append(f"  + {s.replace(' (+)', '')}")
        for s in negative:
            lines.append(f"  - {s}")

        pos_count = len(positive)
        neg_count = len(negative)
        if neg_count == 0:
            lines.append(f"\n  Overall: All systems go — {pos_count} positive signals.")
        elif neg_count <= 1 and pos_count >= 2:
            lines.append(f"\n  Overall: Mostly good — minor concern noted.")
        elif neg_count >= 3:
            lines.append(f"\n  Overall: Multiple caution signals — consider an easier session.")
        else:
            lines.append(f"\n  Overall: Mixed signals — train but monitor intensity.")
    else:
        lines.append("  Insufficient data for readiness assessment.")

    return "\n".join(lines)


@mcp.tool()
def nutrition_performance_correlation(start_date: str = "", end_date: str = "") -> str:
    """Correlate nutrition intake with next-day training metrics.

    Shows whether protein/calorie intake patterns affect next-day body battery,
    HRV, and workout performance. Splits days into high/low intake buckets.

    Args:
        start_date: Start date (YYYY-MM-DD). Default: 30 days ago.
        end_date: End date (YYYY-MM-DD). Default: today.
    """
    start_date, end_date = _parse_date_range(start_date, end_date, 30)

    with get_db(read_only=True) as db:
        # Day-N nutrition paired with day-N+1 Garmin metrics
        pairs = db.execute(
            "SELECT d.date, d.calories_kcal, d.protein_g, d.target_calories, "
            "d.target_protein, d.expenditure, "
            "s.sleep_score, s.avg_overnight_hrv, s.total_sleep_sec, "
            "gs.body_battery_high, gs.resting_hr "
            "FROM daily d "
            "JOIN garmin_sleep s ON s.date = date_add(d.date, INTERVAL 1 DAY) "
            "LEFT JOIN garmin_daily_stats gs ON gs.date = date_add(d.date, INTERVAL 1 DAY) "
            "WHERE d.date BETWEEN ? AND ? "
            "AND d.calories_kcal > 0 AND d.target_calories > 0 AND d.target_protein > 0",
            [start_date, end_date]
        ).fetchall()

    if len(pairs) < 4:
        return (
            f"Not enough paired data ({len(pairs)} days). Need at least 4 days with "
            "both MacroFactor nutrition and next-day Garmin data. "
            "Import MacroFactor exports and sync Garmin data first."
        )

    lines = [
        f"Nutrition → Performance Correlation: {start_date} to {end_date}",
        f"({len(pairs)} paired days analyzed)",
        "=" * 60,
    ]

    # Split by protein adherence
    high_protein = []  # >= 90% of target
    low_protein = []   # < 90% of target
    for row in pairs:
        d, cal, pro, tcal, tpro, exp, score, hrv, sleep_sec, bb, rhr = row
        pro_pct = (pro / tpro * 100) if tpro else 0
        entry = {
            "score": score, "hrv": hrv, "sleep": sleep_sec, "bb": bb, "rhr": rhr
        }
        if pro_pct >= 90:
            high_protein.append(entry)
        else:
            low_protein.append(entry)

    # Split by energy balance
    surplus = []   # cal > expenditure
    deficit = []   # cal <= expenditure
    for row in pairs:
        d, cal, pro, tcal, tpro, exp, score, hrv, sleep_sec, bb, rhr = row
        entry = {
            "score": score, "hrv": hrv, "sleep": sleep_sec, "bb": bb, "rhr": rhr
        }
        balance = cal - (exp or cal)  # if no expenditure, treat as zero balance
        if balance > 0:
            surplus.append(entry)
        else:
            deficit.append(entry)

    def _bucket_avg(entries, key):
        vals = [e[key] for e in entries if e[key] is not None]
        if not vals:
            return None
        return sum(vals) / len(vals)

    def _format_comparison(label, high_entries, low_entries, high_label, low_label):
        result = [f"\n{label}"]
        result.append(
            f"  {'Metric':<20} {high_label:>14} {low_label:>14} {'Diff':>10}"
        )
        result.append("  " + "-" * 62)

        metrics = [
            ("Sleep Score", "score"),
            ("HRV", "hrv"),
            ("Body Battery High", "bb"),
            ("Resting HR", "rhr"),
        ]
        for name, key in metrics:
            h = _bucket_avg(high_entries, key)
            l = _bucket_avg(low_entries, key)
            if h is not None and l is not None:
                diff = h - l
                result.append(
                    f"  {name:<20} {h:>14.1f} {l:>14.1f} {diff:>+10.1f}"
                )
            else:
                result.append(f"  {name:<20} {'N/A':>14} {'N/A':>14} {'':>10}")

        h_sleep = _bucket_avg(high_entries, "sleep")
        l_sleep = _bucket_avg(low_entries, "sleep")
        if h_sleep is not None and l_sleep is not None:
            diff_min = (h_sleep - l_sleep) / 60
            result.append(
                f"  {'Sleep Duration':<20} {_fmt_sleep_sec(int(h_sleep)):>14} "
                f"{_fmt_sleep_sec(int(l_sleep)):>14} {diff_min:>+9.0f}m"
            )

        result.append(f"  (n={len(high_entries)} vs n={len(low_entries)})")
        return result

    lines.extend(_format_comparison(
        "PROTEIN IMPACT (next-day metrics)",
        high_protein, low_protein,
        "High (>=90%)", "Low (<90%)"
    ))

    lines.extend(_format_comparison(
        "ENERGY BALANCE IMPACT (next-day metrics)",
        surplus, deficit,
        "Surplus", "Deficit"
    ))

    # Key takeaways
    lines.append("\nKEY OBSERVATIONS")
    hp_score = _bucket_avg(high_protein, "score")
    lp_score = _bucket_avg(low_protein, "score")
    if hp_score and lp_score:
        if hp_score > lp_score + 3:
            lines.append("  - Higher protein days tend to precede better sleep scores")
        elif lp_score > hp_score + 3:
            lines.append("  - Lower protein days tend to precede better sleep scores")
        else:
            lines.append("  - Protein intake shows minimal impact on next-day sleep score")

    hp_hrv = _bucket_avg(high_protein, "hrv")
    lp_hrv = _bucket_avg(low_protein, "hrv")
    if hp_hrv and lp_hrv:
        if hp_hrv > lp_hrv + 3:
            lines.append("  - Higher protein days tend to precede better HRV")
        elif lp_hrv > hp_hrv + 3:
            lines.append("  - Lower protein days tend to precede better HRV")
        else:
            lines.append("  - Protein intake shows minimal impact on next-day HRV")

    s_bb = _bucket_avg(surplus, "bb")
    d_bb = _bucket_avg(deficit, "bb")
    if s_bb and d_bb:
        if s_bb > d_bb + 3:
            lines.append("  - Caloric surplus days tend to precede higher body battery")
        elif d_bb > s_bb + 3:
            lines.append("  - Caloric deficit days tend to precede higher body battery")
        else:
            lines.append("  - Energy balance shows minimal impact on next-day body battery")

    return "\n".join(lines)


@mcp.tool()
def sleep_analysis(start_date: str = "", end_date: str = "") -> str:
    """Analyze what affects sleep quality — prior-day training, calories, timing.

    Correlates sleep scores with training intensity, total calories, and meal
    timing from the preceding day.

    Args:
        start_date: Start date (YYYY-MM-DD). Default: 30 days ago.
        end_date: End date (YYYY-MM-DD). Default: today.
    """
    start_date, end_date = _parse_date_range(start_date, end_date, 30)
    prior_start = (
        datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=1)
    ).strftime("%Y-%m-%d")

    with get_db(read_only=True) as db:
        # Sleep data
        sleep_rows = db.execute(
            "SELECT date, total_sleep_sec, sleep_score, sleep_score_qualifier, "
            "deep_sleep_sec, rem_sleep_sec, avg_overnight_hrv "
            "FROM garmin_sleep WHERE date BETWEEN ? AND ? ORDER BY date",
            [start_date, end_date]
        ).fetchall()

        if not sleep_rows:
            return f"No sleep data between {start_date} and {end_date}. Run sync_garmin first."

        # Prior-day activities (training days)
        activity_dates_set = set()
        act_rows = db.execute(
            "SELECT DISTINCT date FROM garmin_activities WHERE date BETWEEN ? AND ?",
            [prior_start, end_date]
        ).fetchall()
        for row in act_rows:
            activity_dates_set.add(str(row[0]))

        # Prior-day nutrition
        nutrition_map = {}
        for row in db.execute(
            "SELECT date, calories_kcal, target_calories FROM daily "
            "WHERE date BETWEEN ? AND ?", [prior_start, end_date]
        ).fetchall():
            nutrition_map[str(row[0])] = (row[1] or 0, row[2] or 0)

        # Late eating detection (food log entries after 8pm)
        late_eating_dates = set()
        late_rows = db.execute(
            "SELECT DISTINCT date FROM food_log "
            "WHERE date BETWEEN ? AND ? "
            "AND time IS NOT NULL AND time != '' "
            "AND cast(substr(time, 1, 2) as int) >= 20",
            [prior_start, end_date]
        ).fetchall()
        for row in late_rows:
            late_eating_dates.add(str(row[0]))

    lines = [f"Sleep Analysis: {start_date} to {end_date}", "=" * 60]

    # ── OVERVIEW ──
    durations = [r[1] for r in sleep_rows if r[1]]
    scores = [r[2] for r in sleep_rows if r[2]]

    lines.append(f"\nOVERVIEW ({len(sleep_rows)} nights)")
    if durations:
        avg_dur = sum(durations) / len(durations)
        lines.append(f"  Avg duration: {_fmt_sleep_sec(int(avg_dur))}")
        lines.append(f"  Range: {_fmt_sleep_sec(min(durations))} - {_fmt_sleep_sec(max(durations))}")
    if scores:
        avg_score = sum(scores) / len(scores)
        lines.append(f"  Avg score: {avg_score:.0f}")
        best = max(sleep_rows, key=lambda r: r[2] or 0)
        worst = min(sleep_rows, key=lambda r: r[2] or 999)
        lines.append(f"  Best night: {best[0]} (score {best[2]})")
        lines.append(f"  Worst night: {worst[0]} (score {worst[2]})")
    hrvs = [r[6] for r in sleep_rows if r[6]]
    if hrvs:
        lines.append(f"  Avg HRV: {sum(hrvs) / len(hrvs):.0f}")

    # ── TRAINING DAY IMPACT ──
    lines.append("\nTRAINING DAY IMPACT (sleep after training vs rest days)")
    training_night_sleep = []
    rest_night_sleep = []
    for row in sleep_rows:
        d = str(row[0])
        prior_day = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        entry = {"score": row[2], "duration": row[1], "deep": row[4], "hrv": row[6]}
        if prior_day in activity_dates_set:
            training_night_sleep.append(entry)
        else:
            rest_night_sleep.append(entry)

    def _avg_or_na(entries, key):
        vals = [e[key] for e in entries if e[key] is not None]
        return sum(vals) / len(vals) if vals else None

    if training_night_sleep and rest_night_sleep:
        lines.append(f"  {'Metric':<20} {'After Training':>15} {'After Rest':>12} {'Diff':>8}")
        lines.append("  " + "-" * 58)
        for label, key in [("Score", "score"), ("HRV", "hrv")]:
            t = _avg_or_na(training_night_sleep, key)
            r = _avg_or_na(rest_night_sleep, key)
            if t is not None and r is not None:
                lines.append(f"  {label:<20} {t:>15.1f} {r:>12.1f} {t - r:>+8.1f}")
        t_dur = _avg_or_na(training_night_sleep, "duration")
        r_dur = _avg_or_na(rest_night_sleep, "duration")
        if t_dur is not None and r_dur is not None:
            lines.append(
                f"  {'Duration':<20} {_fmt_sleep_sec(int(t_dur)):>15} "
                f"{_fmt_sleep_sec(int(r_dur)):>12} {(t_dur - r_dur) / 60:>+7.0f}m"
            )
        t_deep = _avg_or_na(training_night_sleep, "deep")
        r_deep = _avg_or_na(rest_night_sleep, "deep")
        if t_deep is not None and r_deep is not None:
            lines.append(
                f"  {'Deep Sleep':<20} {_fmt_sleep_sec(int(t_deep)):>15} "
                f"{_fmt_sleep_sec(int(r_deep)):>12} {(t_deep - r_deep) / 60:>+7.0f}m"
            )
        lines.append(f"  (n={len(training_night_sleep)} training nights, n={len(rest_night_sleep)} rest nights)")
    else:
        lines.append("  Not enough data to compare training vs rest day sleep.")

    # ── CALORIE IMPACT ──
    lines.append("\nCALORIE IMPACT (sleep after over-target vs on-target vs deficit)")
    over_target = []
    on_target = []
    under_target = []
    for row in sleep_rows:
        d = str(row[0])
        prior_day = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        if prior_day not in nutrition_map:
            continue
        cal, tcal = nutrition_map[prior_day]
        if cal == 0 or tcal == 0:
            continue
        entry = {"score": row[2], "duration": row[1], "hrv": row[6]}
        ratio = cal / tcal
        if ratio > 1.10:
            over_target.append(entry)
        elif ratio >= 0.90:
            on_target.append(entry)
        else:
            under_target.append(entry)

    if any([over_target, on_target, under_target]):
        lines.append(f"  {'Metric':<16} {'Over (>110%)':>14} {'On (90-110%)':>14} {'Deficit (<90%)':>15}")
        lines.append("  " + "-" * 62)
        for label, key in [("Score", "score"), ("HRV", "hrv")]:
            o = _avg_or_na(over_target, key)
            on = _avg_or_na(on_target, key)
            u = _avg_or_na(under_target, key)
            o_str = f"{o:.1f}" if o is not None else "N/A"
            on_str = f"{on:.1f}" if on is not None else "N/A"
            u_str = f"{u:.1f}" if u is not None else "N/A"
            lines.append(f"  {label:<16} {o_str:>14} {on_str:>14} {u_str:>15}")
        lines.append(
            f"  (n={len(over_target)} over, n={len(on_target)} on-target, "
            f"n={len(under_target)} deficit)"
        )
    else:
        lines.append("  Not enough paired nutrition + sleep data.")

    # ── LATE EATING IMPACT ──
    lines.append("\nLATE EATING IMPACT (eating after 8pm vs not)")
    late_sleep = []
    early_sleep = []
    for row in sleep_rows:
        d = str(row[0])
        prior_day = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        entry = {"score": row[2], "duration": row[1], "hrv": row[6]}
        if prior_day in late_eating_dates:
            late_sleep.append(entry)
        else:
            early_sleep.append(entry)

    if late_sleep and early_sleep:
        lines.append(f"  {'Metric':<20} {'Late Eating':>14} {'No Late Eating':>15} {'Diff':>8}")
        lines.append("  " + "-" * 60)
        for label, key in [("Score", "score"), ("HRV", "hrv")]:
            l = _avg_or_na(late_sleep, key)
            e = _avg_or_na(early_sleep, key)
            if l is not None and e is not None:
                lines.append(f"  {label:<20} {l:>14.1f} {e:>15.1f} {l - e:>+8.1f}")
        l_dur = _avg_or_na(late_sleep, "duration")
        e_dur = _avg_or_na(early_sleep, "duration")
        if l_dur is not None and e_dur is not None:
            lines.append(
                f"  {'Duration':<20} {_fmt_sleep_sec(int(l_dur)):>14} "
                f"{_fmt_sleep_sec(int(e_dur)):>15} {(l_dur - e_dur) / 60:>+7.0f}m"
            )
        lines.append(f"  (n={len(late_sleep)} late nights, n={len(early_sleep)} early nights)")
    else:
        lines.append("  Not enough food log timing data to compare.")

    return "\n".join(lines)


@mcp.tool()
def body_comp_trend(start_date: str = "", end_date: str = "") -> str:
    """Track body composition over time — weight, body fat, training volume, energy balance.

    Answers: am I gaining muscle or fat? Combines weight trend with body fat,
    training volume, and calorie surplus/deficit.

    Args:
        start_date: Start date (YYYY-MM-DD). Default: 90 days ago.
        end_date: End date (YYYY-MM-DD). Default: today.
    """
    start_date, end_date = _parse_date_range(start_date, end_date, 90)

    with get_db(read_only=True) as db:
        # Daily data
        daily_rows = db.execute(
            "SELECT d.date, d.weight_kg, d.trend_weight_kg, d.fat_percent, "
            "d.calories_kcal, d.expenditure, d.protein_g, "
            "g.body_fat_pct "
            "FROM daily d LEFT JOIN garmin_body_fat g ON d.date = g.date "
            "WHERE d.date BETWEEN ? AND ? ORDER BY d.date",
            [start_date, end_date]
        ).fetchall()

        if not daily_rows:
            return f"No data between {start_date} and {end_date}."

        # Weekly training sets
        training_weeks = db.execute(
            "SELECT date_trunc('week', date) as week_start, "
            "sum(CASE WHEN set_type != 'Warm-Up Set' THEN 1 ELSE 0 END) as work_sets "
            "FROM workouts WHERE date BETWEEN ? AND ? "
            "GROUP BY week_start ORDER BY week_start",
            [start_date, end_date]
        ).fetchall()

    training_by_week = {}
    for week_start, sets in training_weeks:
        training_by_week[str(week_start)[:10]] = sets

    # Aggregate into weekly buckets
    weeks = {}
    for d, wt, tw, bf_mf, cal, exp, pro, bf_g in daily_rows:
        # ISO week start (Monday)
        d_dt = datetime.strptime(str(d), "%Y-%m-%d")
        week_start = (d_dt - timedelta(days=d_dt.weekday())).strftime("%Y-%m-%d")
        w = weeks.setdefault(week_start, {
            "weights": [], "trends": [], "bf": [], "cals": [],
            "exps": [], "proteins": [], "days": 0
        })
        w["days"] += 1
        if wt:
            w["weights"].append(wt)
        if tw:
            w["trends"].append(tw)
        bf = bf_g if bf_g else bf_mf
        if bf:
            w["bf"].append(bf)
        if cal and cal > 0:
            w["cals"].append(cal)
        if exp and exp > 0:
            w["exps"].append(exp)
        if pro and pro > 0:
            w["proteins"].append(pro)

    sorted_weeks = sorted(weeks.keys())

    lines = [
        f"Body Composition Trend: {start_date} to {end_date}",
        "=" * 85,
    ]

    # Weekly table
    lines.append(
        f"\n{'Week':<12} {'Avg Wt':>7} {'Trend':>7} {'BF%':>6} "
        f"{'Avg Bal':>9} {'Avg Pro':>8} {'Sets':>6}"
    )
    lines.append("-" * 60)

    first_trend = None
    last_trend = None
    all_balances = []

    for wk in sorted_weeks:
        w = weeks[wk]
        avg_wt = sum(w["weights"]) / len(w["weights"]) if w["weights"] else None
        avg_tw = sum(w["trends"]) / len(w["trends"]) if w["trends"] else None
        avg_bf = sum(w["bf"]) / len(w["bf"]) if w["bf"] else None
        avg_cal = sum(w["cals"]) / len(w["cals"]) if w["cals"] else 0
        avg_exp = sum(w["exps"]) / len(w["exps"]) if w["exps"] else 0
        avg_pro = sum(w["proteins"]) / len(w["proteins"]) if w["proteins"] else None
        daily_balance = avg_cal - avg_exp if avg_cal and avg_exp else None
        sets = training_by_week.get(wk, 0)

        if avg_tw is not None:
            if first_trend is None:
                first_trend = avg_tw
            last_trend = avg_tw

        if daily_balance is not None:
            all_balances.append(daily_balance)

        wt_str = f"{avg_wt:.1f}" if avg_wt else "-"
        tw_str = f"{avg_tw:.1f}" if avg_tw else "-"
        bf_str = f"{avg_bf:.1f}" if avg_bf else "-"
        bal_str = f"{daily_balance:+.0f}" if daily_balance is not None else "-"
        pro_str = f"{avg_pro:.0f}g" if avg_pro else "-"
        sets_str = f"{sets}" if sets else "-"

        lines.append(
            f"{wk:<12} {wt_str:>7} {tw_str:>7} {bf_str:>6} "
            f"{bal_str:>9} {pro_str:>8} {sets_str:>6}"
        )

    # ── SUMMARY ──
    lines.append(f"\nSUMMARY")

    if first_trend is not None and last_trend is not None:
        weight_change = last_trend - first_trend
        num_weeks = max(len(sorted_weeks), 1)
        rate_per_week = weight_change / num_weeks

        lines.append(f"  Trend weight change: {first_trend:.1f} -> {last_trend:.1f} kg ({weight_change:+.2f} kg)")
        lines.append(f"  Rate: {rate_per_week:+.2f} kg/week")

        if rate_per_week > 0.5:
            lines.append("  Interpretation: rapid gain — likely significant fat accumulation")
        elif rate_per_week > 0.25:
            lines.append("  Interpretation: moderate gain — typical lean bulk range")
        elif rate_per_week > 0.05:
            lines.append("  Interpretation: slow gain — lean bulk or late recomp")
        elif rate_per_week > -0.05:
            lines.append("  Interpretation: weight stable — maintenance or recomposition")
        elif rate_per_week > -0.5:
            lines.append("  Interpretation: moderate loss — typical cut rate")
        else:
            lines.append("  Interpretation: rapid loss — aggressive cut")

    if all_balances:
        avg_daily_balance = sum(all_balances) / len(all_balances)
        lines.append(f"  Avg daily energy balance: {avg_daily_balance:+,.0f} kcal")
        # Expected weight change from energy (7700 kcal per kg)
        total_days = sum(weeks[wk]["days"] for wk in sorted_weeks)
        expected_kg = (avg_daily_balance * total_days) / 7700
        lines.append(f"  Expected change from energy: {expected_kg:+.2f} kg")

        if first_trend is not None and last_trend is not None:
            actual = last_trend - first_trend
            discrepancy = actual - expected_kg
            if abs(discrepancy) > 0.5:
                lines.append(
                    f"  Discrepancy: {discrepancy:+.2f} kg "
                    f"(actual vs expected from energy balance)"
                )
                if discrepancy > 0:
                    lines.append(
                        "  Note: gaining more than energy predicts — could indicate "
                        "water retention, muscle gain, or tracking inaccuracy"
                    )
                else:
                    lines.append(
                        "  Note: losing more than energy predicts — could indicate "
                        "increased NEAT, measurement error, or metabolic adaptation"
                    )

    return "\n".join(lines)
