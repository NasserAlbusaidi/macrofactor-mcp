"""
MacroFactor MCP Server — Garmin Tools

Tools for syncing Garmin data, viewing Garmin health status,
and the cross-data daily briefing.
"""

from datetime import date, timedelta

from main import mcp, get_db, GARMIN_TOKENSTORE
from garmin_sync import (_TRAINING_STATUS_LABELS, _get_garmin_client,
                         _sync_garmin_daily_stats, _sync_garmin_activities,
                         _sync_garmin_sleep, _sync_garmin_training,
                         _sync_garmin_body_fat)


@mcp.tool()
def sync_garmin(days: int = 7) -> str:
    """Sync Garmin health data into local DuckDB for fast cross-queries with MacroFactor data.

    Pulls daily stats, activities, sleep, training status, and body fat
    from Garmin Connect for the last N days. Data is upserted (idempotent).

    Args:
        days: Number of days to sync (default 7).
    """
    if days < 0:
        return "days must be 0 or greater."
    if days > 365:
        return "days is capped at 365 to avoid excessive Garmin API calls."

    garmin = _get_garmin_client()
    if garmin is None:
        return (
            "Garmin not connected. Ensure OAuth tokens exist at "
            f"{GARMIN_TOKENSTORE} (run the Garmin MCP server first to authenticate)."
        )

    end = date.today()
    start = end - timedelta(days=days)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(days + 1)]
    start_str = start.isoformat()
    end_str = end.isoformat()

    errors = []
    counts = {}

    with get_db() as db:
        try:
            counts["stats"] = _sync_garmin_daily_stats(garmin, db, dates)
        except Exception as e:
            errors.append(f"Daily stats: {e}")
            counts["stats"] = 0

        try:
            counts["activities"] = _sync_garmin_activities(garmin, db, start_str, end_str)
        except Exception as e:
            errors.append(f"Activities: {e}")
            counts["activities"] = 0

        try:
            counts["sleep"] = _sync_garmin_sleep(garmin, db, dates)
        except Exception as e:
            errors.append(f"Sleep: {e}")
            counts["sleep"] = 0

        try:
            counts["training"] = _sync_garmin_training(garmin, db, dates)
        except Exception as e:
            errors.append(f"Training: {e}")
            counts["training"] = 0

        try:
            counts["body_fat"] = _sync_garmin_body_fat(garmin, db, start_str, end_str)
        except Exception as e:
            errors.append(f"Body fat: {e}")
            counts["body_fat"] = 0

        # Log the sync
        db.execute(
            "INSERT INTO garmin_sync_log (last_date_synced, stats_rows, "
            "activities_rows, sleep_rows, training_rows) VALUES (?,?,?,?,?)",
            [end_str, counts["stats"], counts["activities"],
             counts["sleep"], counts["training"]],
        )

    lines = [
        f"Garmin sync complete: {start_str} to {end_str}",
        f"  Daily stats:  {counts['stats']} days",
        f"  Activities:   {counts['activities']} activities",
        f"  Sleep:        {counts['sleep']} nights",
        f"  Training:     {counts['training']} days",
        f"  Body fat:     {counts['body_fat']} records",
    ]
    if errors:
        lines.append("\nWarnings:")
        for err in errors:
            lines.append(f"  \u26a0 {err}")

    return "\n".join(lines)


@mcp.tool()
def garmin_status(date_str: str = "") -> str:
    """Quick Garmin health snapshot for a date. No API calls — reads from synced DuckDB data.

    Combines daily stats, sleep, and training status in one view.
    Run sync_garmin first to populate data.

    Args:
        date_str: Date in YYYY-MM-DD format. Empty = today.
    """
    if not date_str:
        date_str = date.today().isoformat()

    with get_db(read_only=True) as db:
        stats = db.execute(
            "SELECT * FROM garmin_daily_stats WHERE date = ?", [date_str]
        ).fetchone()
        stats_cols = [d[0] for d in db.description] if db.description else []

        sleep = db.execute(
            "SELECT * FROM garmin_sleep WHERE date = ?", [date_str]
        ).fetchone()
        sleep_cols = [d[0] for d in db.description] if db.description else []

        training = db.execute(
            "SELECT * FROM garmin_training_status WHERE date = ?", [date_str]
        ).fetchone()
        training_cols = [d[0] for d in db.description] if db.description else []

        activities = db.execute(
            "SELECT activity_name, activity_type, duration_sec, distance_m, "
            "avg_hr, calories FROM garmin_activities WHERE date = ?", [date_str]
        ).fetchall()

    if not stats and not sleep and not training:
        return f"No Garmin data for {date_str}. Run sync_garmin first."

    lines = [f"Garmin Health Snapshot: {date_str}", "=" * 50]

    if stats:
        s = dict(zip(stats_cols, stats))
        lines.append("\nACTIVITY")
        steps = s.get("total_steps") or 0
        goal = s.get("daily_step_goal") or 0
        lines.append(f"  Steps: {steps:,} / {goal:,} goal")
        dist = s.get("distance_meters")
        if dist:
            lines.append(f"  Distance: {dist / 1000:.1f} km")
        tc = s.get("total_calories") or 0
        ac = s.get("active_calories") or 0
        bc = s.get("bmr_calories") or 0
        lines.append(f"  Calories: {tc:,} (active: {ac:,}, BMR: {bc:,})")
        bbh = s.get("body_battery_high")
        bbl = s.get("body_battery_low")
        if bbh is not None:
            lines.append(f"  Body Battery: {bbl}-{bbh}")
        stress = s.get("avg_stress")
        if stress:
            lines.append(f"  Avg Stress: {stress}")
        rhr = s.get("resting_hr")
        if rhr:
            lines.append(f"  Resting HR: {rhr} bpm")

    if sleep:
        sl = dict(zip(sleep_cols, sleep))
        lines.append("\nSLEEP (last night)")
        total_sec = sl.get("total_sleep_sec") or 0
        hrs = total_sec // 3600
        mins = (total_sec % 3600) // 60
        score = sl.get("sleep_score") or "-"
        qual = sl.get("sleep_score_qualifier") or ""
        lines.append(f"  Total: {hrs}h {mins}m (score: {score} {qual})")

        def _fmt_sec(sec):
            if not sec:
                return "-"
            return f"{sec // 3600}h {(sec % 3600) // 60}m"

        deep = _fmt_sec(sl.get("deep_sleep_sec"))
        light = _fmt_sec(sl.get("light_sleep_sec"))
        rem = _fmt_sec(sl.get("rem_sleep_sec"))
        awake = _fmt_sec(sl.get("awake_sec"))
        lines.append(f"  Deep: {deep} | Light: {light} | REM: {rem} | Awake: {awake}")
        hrv = sl.get("avg_overnight_hrv")
        spo2 = sl.get("avg_spo2")
        rhr = sl.get("resting_hr")
        parts = []
        if hrv:
            parts.append(f"HRV: {hrv}")
        if spo2:
            parts.append(f"SpO2: {spo2}%")
        if rhr:
            parts.append(f"Resting HR: {rhr}")
        if parts:
            lines.append(f"  {' | '.join(parts)}")

    if training:
        t = dict(zip(training_cols, training))
        lines.append("\nTRAINING")
        # Make feedback human-readable
        feedback = t.get("training_status_feedback") or ""
        # feedback is like "STRAINED_4" — strip the suffix
        fb_base = feedback.split("_")[0] if feedback else ""
        status_label = _TRAINING_STATUS_LABELS.get(fb_base, feedback)
        lines.append(f"  Status: {status_label}")
        vo2r = t.get("vo2max_running")
        vo2c = t.get("vo2max_cycling")
        if vo2r:
            lines.append(f"  VO2max (running): {vo2r:.1f}")
        if vo2c:
            lines.append(f"  VO2max (cycling): {vo2c:.1f}")
        acute = t.get("acute_load")
        chronic = t.get("chronic_load")
        ratio = t.get("load_ratio")
        if acute and chronic:
            lines.append(f"  Load: {acute:.0f} acute / {chronic:.0f} chronic (ratio: {ratio:.2f})")
        ftp = t.get("ftp_watts")
        ftp_s = t.get("ftp_sport")
        if ftp:
            lines.append(f"  FTP: {ftp}W ({ftp_s})")

    if activities:
        lines.append("\nACTIVITIES")
        for name, atype, dur, dist, hr, cal in activities:
            dur_min = int((dur or 0) / 60)
            dist_km = f"{(dist or 0) / 1000:.1f} km" if dist else ""
            hr_str = f"avg HR {hr}" if hr else ""
            parts = [p for p in [f"{dur_min} min", dist_km, hr_str] if p]
            lines.append(f"  {name or atype} ({', '.join(parts)})")

    return "\n".join(lines)


@mcp.tool()
def daily_briefing(date_str: str = "") -> str:
    """Complete day view combining MacroFactor nutrition + Garmin health data.

    The cross-data tool — shows nutrition, adherence, body, activity,
    sleep, and training in one view. No API calls — reads from DuckDB.

    Args:
        date_str: Date in YYYY-MM-DD format. Empty = today.
    """
    if not date_str:
        date_str = date.today().isoformat()

    with get_db(read_only=True) as db:
        # MacroFactor data
        mf = db.execute(
            "SELECT calories_kcal, protein_g, fat_g, carbs_g, "
            "target_calories, target_protein, target_fat, target_carbs, "
            "weight_kg, trend_weight_kg, expenditure, steps "
            "FROM daily WHERE date = ?", [date_str]
        ).fetchone()

        # Garmin data
        gs = db.execute(
            "SELECT * FROM garmin_daily_stats WHERE date = ?", [date_str]
        ).fetchone()
        gs_cols = [d[0] for d in db.description] if db.description else []

        sl = db.execute(
            "SELECT * FROM garmin_sleep WHERE date = ?", [date_str]
        ).fetchone()
        sl_cols = [d[0] for d in db.description] if db.description else []

        tr = db.execute(
            "SELECT * FROM garmin_training_status WHERE date = ?", [date_str]
        ).fetchone()
        tr_cols = [d[0] for d in db.description] if db.description else []

        bf = db.execute(
            "SELECT body_fat_pct FROM garmin_body_fat WHERE date = ?", [date_str]
        ).fetchone()

        activities = db.execute(
            "SELECT activity_name, activity_type, duration_sec, distance_m, "
            "avg_hr, calories, avg_power FROM garmin_activities WHERE date = ?",
            [date_str],
        ).fetchall()

    if not mf and not gs and not sl:
        return (
            f"No data for {date_str}. "
            "Import a MacroFactor export and/or run sync_garmin first."
        )

    lines = [f"Daily Briefing: {date_str}", "=" * 50]

    # ── Nutrition ──
    if mf:
        cal, pro, fat, carb, t_cal, t_pro, t_fat, t_carb, wt, tw, exp, steps = mf
        lines.append("\nNUTRITION (MacroFactor)")
        lines.append(f"  Intake: {cal:,.0f} kcal  (P {pro:.0f}g / F {fat:.0f}g / C {carb:.0f}g)")
        if t_cal:
            lines.append(f"  Target: {t_cal:,.0f} kcal  (P {t_pro:.0f}g / F {t_fat:.0f}g / C {t_carb:.0f}g)")
            cal_adh = (cal / t_cal * 100) if t_cal else 0
            pro_adh = (pro / t_pro * 100) if t_pro else 0
            lines.append(f"  Adherence: {cal_adh:.0f}% calories | {pro_adh:.0f}% protein")

        # ── Body ──
        lines.append("\nBODY")
        if wt:
            tw_str = f" (trend: {tw:.1f} kg)" if tw else ""
            lines.append(f"  Weight: {wt:.1f} kg{tw_str}")
        if bf:
            lines.append(f"  Body Fat: {bf[0]:.1f}%")
        if exp:
            exp_line = f"  MF Expenditure: {exp:,.0f} kcal"
            if gs:
                g = dict(zip(gs_cols, gs))
                garmin_cal = g.get("total_calories")
                if garmin_cal:
                    exp_line += f" | Garmin Calories: {garmin_cal:,}"
            lines.append(exp_line)

    # ── Activity ──
    lines.append("\nACTIVITY")
    if gs:
        g = dict(zip(gs_cols, gs))
        steps_g = g.get("total_steps") or 0
        goal = g.get("daily_step_goal") or 0
        lines.append(f"  Steps: {steps_g:,} / {goal:,} goal")
        bbh = g.get("body_battery_high")
        bbl = g.get("body_battery_low")
        if bbh is not None:
            lines.append(f"  Body Battery: {bbl} \u2192 {bbh}")
        stress = g.get("avg_stress")
        if stress:
            lines.append(f"  Avg Stress: {stress}")
    elif mf and mf[11]:  # steps from MacroFactor
        lines.append(f"  Steps: {int(mf[11]):,} (MacroFactor)")

    if activities:
        for name, atype, dur, dist, hr, cal, pwr in activities:
            dur_min = int((dur or 0) / 60)
            parts = [f"{dur_min} min"]
            if dist:
                parts.append(f"{dist / 1000:.1f} km")
            if hr:
                parts.append(f"avg HR {hr}")
            if pwr:
                parts.append(f"{pwr:.0f}W")
            lines.append(f"  \u2192 {name or atype} ({', '.join(parts)})")

    # ── Sleep ──
    if sl:
        s = dict(zip(sl_cols, sl))
        lines.append("\nSLEEP")
        total_sec = s.get("total_sleep_sec") or 0
        hrs = total_sec // 3600
        mins = (total_sec % 3600) // 60
        score = s.get("sleep_score") or "-"
        qual = s.get("sleep_score_qualifier") or ""

        def _fmt(sec):
            if not sec:
                return "-"
            return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"

        deep = _fmt(s.get("deep_sleep_sec"))
        rem = _fmt(s.get("rem_sleep_sec"))
        lines.append(f"  {hrs}h {mins}m (score: {score} {qual}) | Deep {deep} | REM {rem}")
        parts = []
        if s.get("avg_overnight_hrv"):
            parts.append(f"HRV: {s['avg_overnight_hrv']}")
        if s.get("resting_hr"):
            parts.append(f"Resting HR: {s['resting_hr']}")
        if s.get("avg_spo2"):
            parts.append(f"SpO2: {s['avg_spo2']}%")
        if parts:
            lines.append(f"  {' | '.join(parts)}")

    # ── Training ──
    if tr:
        t = dict(zip(tr_cols, tr))
        lines.append("\nTRAINING")
        feedback = t.get("training_status_feedback") or ""
        fb_base = feedback.split("_")[0] if feedback else ""
        status_label = _TRAINING_STATUS_LABELS.get(fb_base, feedback)
        vo2r = t.get("vo2max_running")
        vo2c = t.get("vo2max_cycling")
        vo2_parts = []
        if vo2r:
            vo2_parts.append(f"VO2max {vo2r:.1f}")
        if vo2c:
            vo2_parts.append(f"VO2max cycling {vo2c:.1f}")
        vo2_str = " | ".join(vo2_parts)
        lines.append(f"  Status: {status_label}" + (f" | {vo2_str}" if vo2_str else ""))
        acute = t.get("acute_load")
        chronic = t.get("chronic_load")
        ratio = t.get("load_ratio")
        if acute and chronic:
            lines.append(f"  Load: {acute:.0f}/{chronic:.0f} (ACWR {ratio:.2f})")
        ftp = t.get("ftp_watts")
        if ftp:
            lines.append(f"  FTP: {ftp}W ({t.get('ftp_sport', '')})")

    return "\n".join(lines)
