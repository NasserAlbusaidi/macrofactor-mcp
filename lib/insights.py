"""
Personalized Insights Engine

Generates a prioritized personal brief by analyzing cross-domain data:
nutrition, training, sleep, body composition, and recovery.

Uses simple statistics (bucketed comparisons, trend detection, regression)
on the user's own N=1 data to find personal patterns.

Independent of MCP — can be called from the server or local reports.
"""

from datetime import datetime, date, timedelta
from lib.db import get_db


# ═════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _fmt_sleep(sec):
    if not sec:
        return "-"
    return f"{sec // 3600}h {(sec % 3600) // 60}m"


def _safe_avg(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _safe_stdev(values):
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return None
    avg = sum(vals) / len(vals)
    variance = sum((v - avg) ** 2 for v in vals) / len(vals)
    return variance ** 0.5


def _monday_of(d):
    """Return the Monday of the week containing date d."""
    return d - timedelta(days=d.weekday())


def _sparkline(values, width=14):
    """Generate a unicode sparkline from a list of numbers."""
    blocks = "▁▂▃▄▅▆▇█"
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    spread = hi - lo if hi > lo else 1
    return "".join(blocks[min(int((v - lo) / spread * 7), 7)] for v in vals[-width:])


# ═════════════════════════════════════════════════════════════════════════════
#  DATA GATHERING
# ═════════════════════════════════════════════════════════════════════════════

def _gather_current_state(db, today):
    """Gather current state: phase, weight, weekly pacing, training, sleep."""
    state = {}

    # ── Weight trajectory (last 30 days) ──
    weight_rows = db.execute(
        "SELECT date, trend_weight_kg FROM daily "
        "WHERE date >= (CAST(? AS DATE) - INTERVAL 30 DAY) "
        "AND trend_weight_kg IS NOT NULL ORDER BY date",
        [today.isoformat()]
    ).fetchall()

    if len(weight_rows) >= 7:
        first_tw = weight_rows[0][1]
        last_tw = weight_rows[-1][1]
        days_span = (weight_rows[-1][0] - weight_rows[0][0]).days or 1
        rate = (last_tw - first_tw) / days_span * 7
        if rate < -0.15:
            phase = "CUT"
        elif rate > 0.15:
            phase = "BULK"
        else:
            phase = "MAINTENANCE"
        state["weight"] = {
            "current": last_tw,
            "rate_per_week": round(rate, 2),
            "phase": phase,
            "first": first_tw,
            "days": days_span,
        }

    # ── Weekly nutrition pacing ──
    monday = _monday_of(today)
    week_rows = db.execute(
        "SELECT date, calories_kcal, target_calories, protein_g, target_protein "
        "FROM daily WHERE date >= ? AND date <= ? ORDER BY date",
        [monday.isoformat(), today.isoformat()]
    ).fetchall()

    days_elapsed = (today - monday).days + 1
    remaining_days = 7 - days_elapsed
    logged_days = sum(1 for r in week_rows if r[1] and r[1] > 0)
    sum_intake = sum(r[1] or 0 for r in week_rows)
    sum_target = sum(r[2] or 0 for r in week_rows)
    sum_protein = sum(r[3] or 0 for r in week_rows)
    sum_protein_target = sum(r[4] or 0 for r in week_rows)

    # Estimate full-week target from average daily target
    daily_targets = [r[2] for r in week_rows if r[2] and r[2] > 0]
    avg_daily_target = _safe_avg(daily_targets)

    if logged_days > 0 and avg_daily_target:
        weekly_target = avg_daily_target * 7
        needed_remaining = (weekly_target - sum_intake) / remaining_days if remaining_days > 0 else 0
        state["weekly_pacing"] = {
            "day_of_week": days_elapsed,
            "days_logged": logged_days,
            "avg_intake": round(sum_intake / logged_days),
            "avg_target": round(avg_daily_target),
            "weekly_target": round(weekly_target),
            "sum_intake": round(sum_intake),
            "remaining_days": remaining_days,
            "needed_avg_remaining": round(needed_remaining) if remaining_days > 0 else None,
            "avg_protein": round(sum_protein / logged_days),
            "avg_protein_target": round(_safe_avg([r[4] for r in week_rows if r[4]]) or 0),
        }

    # ── Training status (latest) ──
    training = db.execute(
        "SELECT training_status_feedback, acute_load, chronic_load, "
        "load_ratio, vo2max_running, vo2max_cycling, ftp_watts "
        "FROM garmin_training_status WHERE date <= ? "
        "ORDER BY date DESC LIMIT 1",
        [today.isoformat()]
    ).fetchone()

    if training:
        fb = training[0] or ""
        fb_base = fb.split("_")[0]
        labels = {
            "RECOVERY": "Recovery", "PRODUCTIVE": "Productive",
            "MAINTAINING": "Maintaining", "UNPRODUCTIVE": "Unproductive",
            "DETRAINING": "Detraining", "PEAKING": "Peaking",
            "OVERREACHING": "Overreaching", "STRAINED": "Strained",
        }
        state["training"] = {
            "status": labels.get(fb_base, fb),
            "acute": training[1],
            "chronic": training[2],
            "acwr": training[3],
            "vo2max_run": training[4],
            "vo2max_cycle": training[5],
            "ftp": training[6],
        }

    # ── Last night's sleep ──
    sleep = db.execute(
        "SELECT total_sleep_sec, sleep_score, sleep_score_qualifier, "
        "avg_overnight_hrv, resting_hr "
        "FROM garmin_sleep WHERE date = ?",
        [today.isoformat()]
    ).fetchone()

    if sleep:
        state["sleep_last"] = {
            "duration_sec": sleep[0],
            "score": sleep[1],
            "qualifier": sleep[2],
            "hrv": sleep[3],
            "rhr": sleep[4],
        }

    # ── Body battery (today) ──
    bb = db.execute(
        "SELECT body_battery_high, body_battery_low "
        "FROM garmin_daily_stats WHERE date = ?",
        [today.isoformat()]
    ).fetchone()

    if bb and bb[0] is not None:
        state["body_battery"] = {"high": bb[0], "low": bb[1]}

    return state


def _gather_trends(db, today):
    """Compare 3-day rolling avg to 14-day baseline for key metrics."""
    trends = {}

    # Sleep trends
    sleep_rows = db.execute(
        "SELECT date, total_sleep_sec, sleep_score, avg_overnight_hrv "
        "FROM garmin_sleep WHERE date >= (CAST(? AS DATE) - INTERVAL 14 DAY) "
        "AND date <= ? ORDER BY date",
        [today.isoformat(), today.isoformat()]
    ).fetchall()

    if len(sleep_rows) >= 5:
        durations = [r[1] / 3600 if r[1] else None for r in sleep_rows]
        scores = [r[2] for r in sleep_rows]
        hrvs = [r[3] for r in sleep_rows]

        for name, values, higher_is_better in [
            ("sleep_duration", durations, True),
            ("sleep_score", scores, True),
            ("hrv", hrvs, True),
        ]:
            short = _safe_avg(values[-3:])
            baseline = _safe_avg(values)
            stdev = _safe_stdev(values)
            if short is not None and baseline is not None:
                diff = short - baseline
                direction = "stable"
                if stdev and abs(diff) > 0.5 * stdev:
                    direction = "improving" if (diff > 0) == higher_is_better else "declining"
                trends[name] = {
                    "short": round(short, 1),
                    "baseline": round(baseline, 1),
                    "direction": direction,
                    "diff": round(diff, 1),
                    "sparkline": _sparkline(values),
                }

    # Body battery trend
    bb_rows = db.execute(
        "SELECT date, body_battery_high "
        "FROM garmin_daily_stats WHERE date >= (CAST(? AS DATE) - INTERVAL 14 DAY) "
        "AND date <= ? AND body_battery_high IS NOT NULL ORDER BY date",
        [today.isoformat(), today.isoformat()]
    ).fetchall()

    if len(bb_rows) >= 5:
        bb_vals = [r[1] for r in bb_rows]
        short = _safe_avg(bb_vals[-3:])
        baseline = _safe_avg(bb_vals)
        stdev = _safe_stdev(bb_vals)
        if short is not None and baseline is not None:
            diff = short - baseline
            direction = "stable"
            if stdev and abs(diff) > 0.5 * stdev:
                direction = "improving" if diff > 0 else "declining"
            trends["body_battery"] = {
                "short": round(short, 1),
                "baseline": round(baseline, 1),
                "direction": direction,
                "diff": round(diff, 1),
                "sparkline": _sparkline(bb_vals),
            }

    # Compliance trends (last 14 days)
    compliance_rows = db.execute(
        "SELECT date, calories_kcal, target_calories, protein_g, target_protein "
        "FROM daily WHERE date >= (CAST(? AS DATE) - INTERVAL 14 DAY) "
        "AND date <= ? AND target_calories > 0 ORDER BY date",
        [today.isoformat(), today.isoformat()]
    ).fetchall()

    if len(compliance_rows) >= 5:
        cal_hits = []
        pro_hits = []
        for _, cal, tcal, pro, tpro in compliance_rows:
            cal, tcal = cal or 0, tcal or 0
            pro, tpro = pro or 0, tpro or 0
            cal_hits.append(1 if tcal > 0 and cal > 0 and abs(cal - tcal) / tcal <= 0.10 else 0)
            pro_hits.append(1 if tpro > 0 and pro >= tpro * 0.90 else 0)

        # Use 5-day rolling windows for trend
        if len(cal_hits) >= 7:
            recent_cal = _safe_avg(cal_hits[-5:])
            overall_cal = _safe_avg(cal_hits)
            if recent_cal is not None and overall_cal is not None:
                diff = recent_cal - overall_cal
                direction = "stable"
                if abs(diff) > 0.15:
                    direction = "improving" if diff > 0 else "declining"
                trends["cal_compliance"] = {
                    "short": round(recent_cal * 100),
                    "baseline": round(overall_cal * 100),
                    "direction": direction,
                    "diff": round(diff * 100),
                    "unit": "%",
                }

            recent_pro = _safe_avg(pro_hits[-5:])
            overall_pro = _safe_avg(pro_hits)
            if recent_pro is not None and overall_pro is not None:
                diff = recent_pro - overall_pro
                direction = "stable"
                if abs(diff) > 0.15:
                    direction = "improving" if diff > 0 else "declining"
                trends["pro_compliance"] = {
                    "short": round(recent_pro * 100),
                    "baseline": round(overall_pro * 100),
                    "direction": direction,
                    "diff": round(diff * 100),
                    "unit": "%",
                }

    # Weight rate trend
    weight_rows = db.execute(
        "SELECT date, trend_weight_kg FROM daily "
        "WHERE date >= (CAST(? AS DATE) - INTERVAL 14 DAY) "
        "AND date <= ? AND trend_weight_kg IS NOT NULL ORDER BY date",
        [today.isoformat(), today.isoformat()]
    ).fetchall()

    if len(weight_rows) >= 5:
        weights = [r[1] for r in weight_rows]
        trends["weight"] = {
            "sparkline": _sparkline(weights),
            "direction": "rising" if weights[-1] > weights[0] + 0.1 else
                         "falling" if weights[-1] < weights[0] - 0.1 else "stable",
        }

    return trends


def _compute_correlations(db, today):
    """Compute personal correlations from historical data."""
    correlations = {}

    # Need at least 14 paired data points for any correlation
    pairs = db.execute(
        "SELECT d.date, d.calories_kcal, d.protein_g, d.target_calories, "
        "d.target_protein, d.expenditure, "
        "s.sleep_score, s.avg_overnight_hrv, s.total_sleep_sec, "
        "gs.body_battery_high "
        "FROM daily d "
        "JOIN garmin_sleep s ON s.date = date_add(d.date, INTERVAL 1 DAY) "
        "LEFT JOIN garmin_daily_stats gs ON gs.date = date_add(d.date, INTERVAL 1 DAY) "
        "WHERE d.date >= (CAST(? AS DATE) - INTERVAL 90 DAY) "
        "AND d.date <= ? "
        "AND d.calories_kcal > 0 AND d.target_protein > 0",
        [today.isoformat(), today.isoformat()]
    ).fetchall()

    if len(pairs) >= 14:
        # ── Protein → next-day recovery ──
        high_pro = [p for p in pairs if p[4] and p[2] and p[2] >= p[4] * 0.90]
        low_pro = [p for p in pairs if p[4] and p[2] and p[2] < p[4] * 0.90]

        if len(high_pro) >= 5 and len(low_pro) >= 5:
            hp_score = _safe_avg([p[6] for p in high_pro])
            lp_score = _safe_avg([p[6] for p in low_pro])
            hp_hrv = _safe_avg([p[7] for p in high_pro])
            lp_hrv = _safe_avg([p[7] for p in low_pro])
            hp_bb = _safe_avg([p[9] for p in high_pro])
            lp_bb = _safe_avg([p[9] for p in low_pro])

            correlations["protein_recovery"] = {
                "high": {"score": hp_score, "hrv": hp_hrv, "bb": hp_bb, "n": len(high_pro)},
                "low": {"score": lp_score, "hrv": lp_hrv, "bb": lp_bb, "n": len(low_pro)},
                "score_diff": round(hp_score - lp_score, 1) if hp_score and lp_score else None,
                "hrv_diff": round(hp_hrv - lp_hrv, 1) if hp_hrv and lp_hrv else None,
                "bb_diff": round(hp_bb - lp_bb, 1) if hp_bb and lp_bb else None,
            }

        # ── Energy balance → next-day recovery ──
        surplus = [p for p in pairs if p[5] and p[1] > p[5]]
        deficit = [p for p in pairs if p[5] and p[1] <= p[5]]

        if len(surplus) >= 5 and len(deficit) >= 5:
            s_bb = _safe_avg([p[9] for p in surplus])
            d_bb = _safe_avg([p[9] for p in deficit])
            s_hrv = _safe_avg([p[7] for p in surplus])
            d_hrv = _safe_avg([p[7] for p in deficit])

            correlations["energy_balance_recovery"] = {
                "surplus": {"bb": s_bb, "hrv": s_hrv, "n": len(surplus)},
                "deficit": {"bb": d_bb, "hrv": d_hrv, "n": len(deficit)},
                "bb_diff": round(s_bb - d_bb, 1) if s_bb and d_bb else None,
                "hrv_diff": round(s_hrv - d_hrv, 1) if s_hrv and d_hrv else None,
            }

    # ── Training day → same-night sleep ──
    activity_dates = set()
    for row in db.execute(
        "SELECT DISTINCT date FROM garmin_activities "
        "WHERE date >= (CAST(? AS DATE) - INTERVAL 90 DAY) AND date <= ?",
        [today.isoformat(), today.isoformat()]
    ).fetchall():
        activity_dates.add(str(row[0]))

    sleep_rows = db.execute(
        "SELECT date, sleep_score, total_sleep_sec, avg_overnight_hrv "
        "FROM garmin_sleep "
        "WHERE date >= (CAST(? AS DATE) - INTERVAL 90 DAY) AND date <= ?",
        [today.isoformat(), today.isoformat()]
    ).fetchall()

    if len(sleep_rows) >= 14:
        training_nights = []
        rest_nights = []
        for row in sleep_rows:
            d = str(row[0])
            prior = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            entry = {"score": row[1], "duration": row[2], "hrv": row[3]}
            if prior in activity_dates:
                training_nights.append(entry)
            else:
                rest_nights.append(entry)

        if len(training_nights) >= 5 and len(rest_nights) >= 5:
            t_score = _safe_avg([e["score"] for e in training_nights])
            r_score = _safe_avg([e["score"] for e in rest_nights])
            t_dur = _safe_avg([e["duration"] for e in training_nights])
            r_dur = _safe_avg([e["duration"] for e in rest_nights])

            correlations["training_sleep"] = {
                "training": {"score": t_score, "duration_sec": t_dur, "n": len(training_nights)},
                "rest": {"score": r_score, "duration_sec": r_dur, "n": len(rest_nights)},
                "score_diff": round(t_score - r_score, 1) if t_score and r_score else None,
                "duration_diff_min": round((t_dur - r_dur) / 60, 0) if t_dur and r_dur else None,
            }

    # ── Late eating → sleep ──
    late_eating_dates = set()
    for row in db.execute(
        "SELECT DISTINCT date FROM food_log "
        "WHERE date >= (CAST(? AS DATE) - INTERVAL 90 DAY) AND date <= ? "
        "AND time IS NOT NULL AND time != '' "
        "AND cast(substr(time, 1, 2) as int) >= 20",
        [today.isoformat(), today.isoformat()]
    ).fetchall():
        late_eating_dates.add(str(row[0]))

    if len(sleep_rows) >= 14 and late_eating_dates:
        late_nights = []
        early_nights = []
        for row in sleep_rows:
            d = str(row[0])
            prior = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            entry = {"score": row[1], "hrv": row[3]}
            if prior in late_eating_dates:
                late_nights.append(entry)
            else:
                early_nights.append(entry)

        if len(late_nights) >= 3 and len(early_nights) >= 5:
            l_score = _safe_avg([e["score"] for e in late_nights])
            e_score = _safe_avg([e["score"] for e in early_nights])

            correlations["late_eating_sleep"] = {
                "late": {"score": l_score, "n": len(late_nights)},
                "early": {"score": e_score, "n": len(early_nights)},
                "score_diff": round(l_score - e_score, 1) if l_score and e_score else None,
            }

    # ── Personal TDEE estimation ──
    tdee_rows = db.execute(
        "SELECT calories_kcal, expenditure, trend_weight_kg FROM daily "
        "WHERE date >= (CAST(? AS DATE) - INTERVAL 60 DAY) "
        "AND date <= ? AND calories_kcal > 0 AND trend_weight_kg IS NOT NULL "
        "ORDER BY date",
        [today.isoformat(), today.isoformat()]
    ).fetchall()

    if len(tdee_rows) >= 21:
        total_intake = sum(r[0] for r in tdee_rows)
        mf_expenditure_avg = _safe_avg([r[1] for r in tdee_rows if r[1]])
        weight_change = tdee_rows[-1][2] - tdee_rows[0][2]
        n_days = len(tdee_rows)
        # TDEE = (total_intake - weight_change_kg * 7700) / days
        estimated_tdee = (total_intake - weight_change * 7700) / n_days

        correlations["personal_tdee"] = {
            "estimated": round(estimated_tdee),
            "mf_avg": round(mf_expenditure_avg) if mf_expenditure_avg else None,
            "difference": round(estimated_tdee - mf_expenditure_avg) if mf_expenditure_avg else None,
            "weight_change_kg": round(weight_change, 2),
            "data_days": n_days,
        }

    # ── Training-day calorie overshoot ──
    if activity_dates:
        training_day_intake = db.execute(
            "SELECT d.calories_kcal, d.target_calories "
            "FROM daily d "
            "JOIN garmin_activities g ON d.date = g.date "
            "WHERE d.date >= (CAST(? AS DATE) - INTERVAL 90 DAY) "
            "AND d.date <= ? AND d.calories_kcal > 0 AND d.target_calories > 0 "
            "GROUP BY d.date, d.calories_kcal, d.target_calories",
            [today.isoformat(), today.isoformat()]
        ).fetchall()

        rest_day_intake = db.execute(
            "SELECT d.calories_kcal, d.target_calories "
            "FROM daily d "
            "WHERE d.date >= (CAST(? AS DATE) - INTERVAL 90 DAY) "
            "AND d.date <= ? AND d.calories_kcal > 0 AND d.target_calories > 0 "
            "AND d.date NOT IN (SELECT DISTINCT date FROM garmin_activities "
            "    WHERE date >= (CAST(? AS DATE) - INTERVAL 90 DAY) AND date <= ?)",
            [today.isoformat(), today.isoformat(), today.isoformat(), today.isoformat()]
        ).fetchall()

        if len(training_day_intake) >= 5 and len(rest_day_intake) >= 5:
            train_avg = _safe_avg([r[0] for r in training_day_intake])
            train_target = _safe_avg([r[1] for r in training_day_intake])
            rest_avg = _safe_avg([r[0] for r in rest_day_intake])
            rest_target = _safe_avg([r[1] for r in rest_day_intake])

            if train_avg and rest_avg:
                correlations["training_day_eating"] = {
                    "training_avg": round(train_avg),
                    "training_target": round(train_target) if train_target else None,
                    "rest_avg": round(rest_avg),
                    "rest_target": round(rest_target) if rest_target else None,
                    "overshoot": round(train_avg - rest_avg),
                    "n_training": len(training_day_intake),
                    "n_rest": len(rest_day_intake),
                }

    return correlations


# ═════════════════════════════════════════════════════════════════════════════
#  RECOMMENDATIONS
# ═════════════════════════════════════════════════════════════════════════════

def _generate_recommendations(state, trends, correlations):
    """Synthesize all signals into prioritized recommendations."""
    recs = []  # (priority, message, reason)

    # ── Sleep-based ──
    sleep = state.get("sleep_last", {})
    sleep_trend = trends.get("sleep_duration", {})

    if sleep.get("score") and sleep["score"] < 50:
        recs.append((1, "Prioritize sleep tonight", f"Last score: {sleep['score']}"))

    if sleep_trend.get("direction") == "declining" and abs(sleep_trend.get("diff", 0)) > 0.3:
        recs.append((1,
            f"Sleep has dropped {abs(sleep_trend['diff']):.1f}h/night recently",
            f"3-day avg: {sleep_trend['short']:.1f}h vs baseline: {sleep_trend['baseline']:.1f}h"
        ))

    # ── Training load ──
    training = state.get("training", {})
    acwr = training.get("acwr")

    if acwr and acwr > 1.5:
        recs.append((1, "High injury risk — consider rest",
                     f"ACWR at {acwr:.2f} (danger zone >1.5)"))
    elif acwr and acwr > 1.3:
        recs.append((2, "Elevated training load — train with caution",
                     f"ACWR at {acwr:.2f} (caution zone >1.3)"))

    if training.get("status") in ("Strained", "Overreaching"):
        recs.append((1, f"Garmin status: {training['status']} — back off intensity",
                     "Multiple fatigue markers elevated"))

    # ── Body battery ──
    bb = state.get("body_battery", {})
    if bb.get("high") and bb["high"] < 40:
        recs.append((2, "Low body battery — consider an easy day",
                     f"Peak only reached {bb['high']} today"))

    # ── Nutrition pacing ──
    pacing = state.get("weekly_pacing", {})
    if pacing.get("needed_avg_remaining"):
        needed = pacing["needed_avg_remaining"]
        avg_target = pacing.get("avg_target", 0)
        avg_intake = pacing.get("avg_intake", 0)

        if avg_target > 0 and avg_intake > avg_target * 1.15 and pacing["remaining_days"] > 0:
            recs.append((2,
                f"To hit weekly target, average {needed:,} kcal for the remaining {pacing['remaining_days']} days",
                f"Currently averaging {avg_intake:,} kcal vs {avg_target:,} target"
            ))
        elif avg_target > 0 and avg_intake < avg_target * 0.85 and pacing["remaining_days"] > 0:
            recs.append((3,
                f"Intake running low — you can eat {needed:,} kcal/day and still hit weekly target",
                f"Currently averaging {avg_intake:,} kcal vs {avg_target:,} target"
            ))

    # ── Protein shortfall ──
    if pacing.get("avg_protein") and pacing.get("avg_protein_target"):
        if pacing["avg_protein"] < pacing["avg_protein_target"] * 0.85:
            recs.append((2,
                f"Protein running low this week ({pacing['avg_protein']}g vs {pacing['avg_protein_target']}g target)",
                "Protein supports recovery between sessions"
            ))

    # ── Correlation-based ──
    protein_rec = correlations.get("protein_recovery", {})
    if protein_rec.get("score_diff") and protein_rec["score_diff"] > 3:
        recs.append((3,
            f"High protein days give you +{protein_rec['score_diff']:.0f} pts on next-day sleep score",
            f"Based on {protein_rec['high']['n']} high vs {protein_rec['low']['n']} low protein days"
        ))

    late_eating = correlations.get("late_eating_sleep", {})
    if late_eating.get("score_diff") and late_eating["score_diff"] < -3:
        recs.append((3,
            f"Late eating (after 8pm) costs you {abs(late_eating['score_diff']):.0f} pts on sleep score",
            f"Based on {late_eating['late']['n']} late vs {late_eating['early']['n']} early nights"
        ))

    training_eating = correlations.get("training_day_eating", {})
    if training_eating.get("overshoot") and training_eating["overshoot"] > 200:
        recs.append((3,
            f"You eat ~{training_eating['overshoot']:,} kcal more on training days (target doesn't adjust)",
            f"Training day avg: {training_eating['training_avg']:,} vs rest: {training_eating['rest_avg']:,}"
        ))

    # ── Positive reinforcement ──
    pro_trend = trends.get("pro_compliance", {})
    if pro_trend.get("direction") == "improving":
        recs.append((4, f"Protein compliance improving ({pro_trend['short']}% recent vs {pro_trend['baseline']}% baseline)",
                     "Keep it up"))

    bb_trend = trends.get("body_battery", {})
    if bb_trend.get("direction") == "improving":
        recs.append((4, "Body battery trending up — recovery is going well", ""))

    if acwr and 0.8 <= acwr <= 1.3:
        recs.append((4, f"Training load in optimal zone (ACWR {acwr:.2f})", ""))

    recs.sort(key=lambda r: r[0])
    return recs


# ═════════════════════════════════════════════════════════════════════════════
#  FORMATTING
# ═════════════════════════════════════════════════════════════════════════════

def _format_current_state(state):
    lines = ["## Current State"]

    w = state.get("weight", {})
    if w:
        phase_label = {"CUT": "cutting", "BULK": "lean bulk", "MAINTENANCE": "maintenance"}.get(
            w.get("phase"), "unknown"
        )
        lines.append(
            f"- Phase: **{phase_label}** | Weight: {w['current']:.1f} kg "
            f"(trending {w['rate_per_week']:+.2f} kg/wk)"
        )

    pacing = state.get("weekly_pacing", {})
    if pacing:
        lines.append(
            f"- This week: {pacing['days_logged']}/7 days logged, "
            f"averaging {pacing['avg_intake']:,} kcal (target {pacing['avg_target']:,})"
        )
        if pacing.get("needed_avg_remaining") and pacing["remaining_days"] > 0:
            lines.append(
                f"- Remaining {pacing['remaining_days']} days need avg "
                f"{pacing['needed_avg_remaining']:,} kcal to hit weekly target"
            )

    t = state.get("training", {})
    if t:
        parts = [f"Training: **{t['status']}**"]
        if t.get("acwr"):
            parts.append(f"ACWR {t['acwr']:.2f}")
        if t.get("vo2max_run"):
            parts.append(f"VO2max {t['vo2max_run']:.1f}")
        if t.get("ftp"):
            parts.append(f"FTP {t['ftp']}W")
        lines.append("- " + " | ".join(parts))

    sl = state.get("sleep_last", {})
    if sl:
        parts = []
        if sl.get("duration_sec"):
            parts.append(_fmt_sleep(sl["duration_sec"]))
        if sl.get("score"):
            parts.append(f"score {sl['score']} {sl.get('qualifier', '')}")
        if sl.get("hrv"):
            parts.append(f"HRV {sl['hrv']}")
        if parts:
            lines.append(f"- Last sleep: {' | '.join(parts)}")

    bb = state.get("body_battery", {})
    if bb:
        lines.append(f"- Body battery: {bb['low']} → {bb['high']}")

    return "\n".join(lines)


def _format_attention(trends):
    declining = {k: v for k, v in trends.items() if v.get("direction") == "declining"}
    if not declining:
        return None

    lines = ["## Needs Attention"]
    for name, t in declining.items():
        label = {
            "sleep_duration": "Sleep duration",
            "sleep_score": "Sleep score",
            "hrv": "HRV",
            "body_battery": "Body battery",
            "cal_compliance": "Calorie compliance",
            "pro_compliance": "Protein compliance",
        }.get(name, name)
        unit = t.get("unit", "")
        spark = t.get("sparkline", "")
        lines.append(
            f"- **{label}** declining: {t['short']}{unit} recent vs "
            f"{t['baseline']}{unit} baseline ({t['diff']:+}{unit})"
        )
        if spark:
            lines.append(f"  {spark}")

    return "\n".join(lines)


def _format_correlations(correlations):
    if not correlations:
        return None

    lines = ["## Your Patterns"]

    tdee = correlations.get("personal_tdee")
    if tdee:
        lines.append(
            f"- Your actual TDEE: ~{tdee['estimated']:,} kcal"
        )
        if tdee.get("mf_avg"):
            diff_str = f"{tdee['difference']:+,}" if tdee['difference'] else ""
            lines.append(
                f"  (MacroFactor estimates {tdee['mf_avg']:,} kcal — "
                f"difference: {diff_str} kcal, from {tdee['data_days']} days of data)"
            )

    pr = correlations.get("protein_recovery")
    if pr:
        parts = []
        if pr.get("score_diff") and abs(pr["score_diff"]) > 2:
            parts.append(f"{pr['score_diff']:+.0f} pts sleep score")
        if pr.get("hrv_diff") and abs(pr["hrv_diff"]) > 2:
            parts.append(f"{pr['hrv_diff']:+.0f} HRV")
        if pr.get("bb_diff") and abs(pr["bb_diff"]) > 3:
            parts.append(f"{pr['bb_diff']:+.0f} body battery")
        if parts:
            lines.append(
                f"- High protein days → next-day: {', '.join(parts)} "
                f"(n={pr['high']['n']} vs {pr['low']['n']})"
            )

    eb = correlations.get("energy_balance_recovery")
    if eb:
        parts = []
        if eb.get("bb_diff") and abs(eb["bb_diff"]) > 3:
            label = "surplus" if eb["bb_diff"] > 0 else "deficit"
            parts.append(f"body battery {eb['bb_diff']:+.0f} on {label} days")
        if parts:
            lines.append(
                f"- Energy balance → next-day: {', '.join(parts)} "
                f"(n={eb['surplus']['n']} surplus vs {eb['deficit']['n']} deficit)"
            )

    ts = correlations.get("training_sleep")
    if ts:
        parts = []
        if ts.get("score_diff") and abs(ts["score_diff"]) > 2:
            better = "better" if ts["score_diff"] > 0 else "worse"
            parts.append(f"{better} sleep score ({ts['score_diff']:+.0f})")
        if ts.get("duration_diff_min") and abs(ts["duration_diff_min"]) > 10:
            parts.append(f"{ts['duration_diff_min']:+.0f} min duration")
        if parts:
            lines.append(
                f"- Training days → sleep: {', '.join(parts)} "
                f"(n={ts['training']['n']} training vs {ts['rest']['n']} rest nights)"
            )

    le = correlations.get("late_eating_sleep")
    if le and le.get("score_diff") and abs(le["score_diff"]) > 2:
        lines.append(
            f"- Late eating (after 8pm) → sleep score {le['score_diff']:+.0f} pts "
            f"(n={le['late']['n']} late vs {le['early']['n']} early)"
        )

    te = correlations.get("training_day_eating")
    if te and te.get("overshoot") and abs(te["overshoot"]) > 100:
        lines.append(
            f"- Training days: you eat ~{te['overshoot']:+,} kcal vs rest days "
            f"({te['training_avg']:,} vs {te['rest_avg']:,})"
        )

    return "\n".join(lines) if len(lines) > 1 else None


def _format_recommendations(recs):
    if not recs:
        return ""

    lines = ["## Recommendations"]
    priority_labels = {1: "!", 2: ".", 3: ".", 4: "+"}

    for priority, message, reason in recs:
        marker = priority_labels.get(priority, ".")
        if priority <= 2:
            lines.append(f"- **{message}**")
        elif priority == 4:
            lines.append(f"- {message}")
        else:
            lines.append(f"- {message}")
        if reason and priority <= 3:
            lines.append(f"  _{reason}_")

    return "\n".join(lines)


def _format_trends(trends):
    if not trends:
        return ""

    lines = ["## Trends (14-day)"]
    display_order = [
        ("sleep_score", "Sleep"),
        ("sleep_duration", "Duration"),
        ("hrv", "HRV"),
        ("body_battery", "Battery"),
        ("weight", "Weight"),
        ("pro_compliance", "Protein"),
        ("cal_compliance", "Calories"),
    ]

    for key, label in display_order:
        t = trends.get(key)
        if not t:
            continue
        spark = t.get("sparkline", "")
        direction = t.get("direction", "")
        arrow = {"improving": "^", "declining": "v", "stable": "-",
                 "rising": "^", "falling": "v"}.get(direction, " ")
        if spark:
            lines.append(f"  {label:<10} {spark}  {arrow} {direction}")

    return "\n".join(lines) if len(lines) > 1 else ""


def _format_data_summary(state):
    """Show data availability at the bottom."""
    parts = []
    w = state.get("weight", {})
    if w.get("days"):
        parts.append(f"Weight: {w['days']} days")
    if state.get("training"):
        parts.append("Garmin: synced")
    if state.get("sleep_last"):
        parts.append("Sleep: synced")

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"---\n_Generated: {generated} | Data: {', '.join(parts)}_"


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def generate_insights(read_only: bool = False) -> str:
    """Generate personalized insights from all available data.

    Returns a markdown document with sections:
    - Current State
    - Needs Attention (declining trends)
    - Your Patterns (personal correlations)
    - Recommendations
    - Trends (sparklines)

    Can be called from MCP or another local Python process.
    """
    today = date.today()

    with get_db(read_only=read_only) as db:
        state = _gather_current_state(db, today)
        trends = _gather_trends(db, today)
        correlations = _compute_correlations(db, today)

    recommendations = _generate_recommendations(state, trends, correlations)

    sections = []
    sections.append(f"# Personal Insights — {today.isoformat()}")
    sections.append(_format_current_state(state))

    attention = _format_attention(trends)
    if attention:
        sections.append(attention)

    patterns = _format_correlations(correlations)
    if patterns:
        sections.append(patterns)

    if recommendations:
        sections.append(_format_recommendations(recommendations))

    trend_display = _format_trends(trends)
    if trend_display:
        sections.append(trend_display)

    sections.append(_format_data_summary(state))

    return "\n\n".join(sections)
