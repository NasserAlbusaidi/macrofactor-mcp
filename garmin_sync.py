"""
MacroFactor + Garmin MCP Server — Garmin Sync Layer

Handles syncing data from Garmin Connect into DuckDB tables.
"""

import time
import logging
from datetime import datetime

logger = logging.getLogger("macrofactor-mcp")

_TRAINING_STATUS_LABELS = {
    "RECOVERY": "Recovery", "PRODUCTIVE": "Productive",
    "MAINTAINING": "Maintaining", "UNPRODUCTIVE": "Unproductive",
    "DETRAINING": "Detraining", "PEAKING": "Peaking",
    "OVERREACHING": "Overreaching", "STRAINED": "Strained",
}

# ── Garmin Client ────────────────────────────────────────────────────────────
_garmin_client = None
_garmin_init_attempted = False


def _get_garmin_client(force_refresh=False):
    """Lazy-initialize and return the Garmin client, or None if unavailable.

    Args:
        force_refresh: If True, discard the cached client and re-login.
                       Used when a token expiry is detected.
    """
    from lib.db import GARMIN_TOKENSTORE
    global _garmin_client, _garmin_init_attempted

    if force_refresh:
        _garmin_client = None
        _garmin_init_attempted = False

    if _garmin_client is not None:
        return _garmin_client
    if _garmin_init_attempted:
        return None

    _garmin_init_attempted = True
    try:
        from garminconnect import Garmin
        garmin = Garmin()
        garmin.login(GARMIN_TOKENSTORE)
        _garmin_client = garmin
        logger.info("Garmin client initialized successfully")
        return _garmin_client
    except Exception as e:
        logger.warning("Garmin client not available: %s", e)
        return None


def _garmin_api_call(func, *args, **kwargs):
    """Call a Garmin API function with automatic token refresh on auth failure.

    If the call fails with an auth/token error, resets the client and retries once.
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        err_str = str(e).lower()
        if any(tok in err_str for tok in ("401", "unauthorized", "token", "expired", "login")):
            logger.warning("Garmin auth error, refreshing token and retrying: %s", e)
            refreshed = _get_garmin_client(force_refresh=True)
            if refreshed is None:
                raise
            # Re-bind the method to the new client and retry
            method_name = getattr(func, '__name__', None) or getattr(func, '__func__', lambda: None).__name__
            new_func = getattr(refreshed, method_name, None)
            if new_func:
                return new_func(*args, **kwargs)
        raise


def _reset_garmin_client():
    """Reset the cached client so next call attempts fresh login."""
    global _garmin_client, _garmin_init_attempted
    _garmin_client = None
    _garmin_init_attempted = False


# ═════════════════════════════════════════════════════════════════════════════
#  GARMIN SYNC LAYER
# ═════════════════════════════════════════════════════════════════════════════


def _sync_garmin_daily_stats(garmin, db, dates):
    """Sync daily stats from Garmin for a list of date strings."""
    count = 0
    for d in dates:
        try:
            s = _garmin_api_call(garmin.get_stats, d)
            if not s:
                continue
            db.execute(
                "INSERT OR REPLACE INTO garmin_daily_stats VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    d,
                    s.get("totalSteps"),
                    s.get("dailyStepGoal"),
                    s.get("totalDistanceMeters"),
                    s.get("floorsAscended"),
                    s.get("floorsDescended"),
                    s.get("totalKilocalories"),
                    s.get("activeKilocalories"),
                    s.get("bmrKilocalories"),
                    s.get("moderateIntensityMinutes"),
                    s.get("vigorousIntensityMinutes"),
                    s.get("restingHeartRate"),
                    s.get("averageStressLevel"),
                    s.get("maxStressLevel"),
                    s.get("bodyBatteryHighestValue"),
                    s.get("bodyBatteryLowestValue"),
                ],
            )
            count += 1
        except Exception as e:
            logger.warning("Failed to sync stats for %s: %s", d, e)
        time.sleep(0.3)
    return count


def _sync_garmin_activities(garmin, db, start_date, end_date):
    """Sync activities from Garmin for a date range."""
    count = 0
    try:
        activities = _garmin_api_call(garmin.get_activities_by_date, start_date, end_date)
        if not activities:
            return 0
        for a in activities:
            start_local = a.get("startTimeLocal", "")
            act_date = start_local[:10] if start_local else None
            act_type = a.get("activityType", {})
            type_key = act_type.get("typeKey", "") if isinstance(act_type, dict) else ""

            # Cadence: running and cycling use different API field names
            cadence = (
                a.get("averageRunningCadenceInStepsPerMinute")
                or a.get("averageBikingCadenceInRevPerMinute")
            )

            db.execute(
                "INSERT OR REPLACE INTO garmin_activities VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    a.get("activityId"),
                    act_date,
                    a.get("activityName"),
                    type_key,
                    a.get("duration"),
                    a.get("distance"),
                    a.get("averageHR"),
                    a.get("maxHR"),
                    a.get("calories"),
                    a.get("averageSpeed"),
                    a.get("maxSpeed"),
                    a.get("averagePower"),
                    a.get("maxPower"),
                    a.get("normPower"),
                    a.get("aerobicTrainingEffect"),
                    a.get("anaerobicTrainingEffect"),
                    # ── dynamics fields ──
                    a.get("elevationGain"),
                    a.get("vO2MaxValue"),
                    cadence,
                    a.get("avgGroundContactTime"),
                    a.get("avgStrideLength"),
                    a.get("avgVerticalOscillation"),
                    a.get("avgVerticalRatio"),
                    a.get("avgGradeAdjustedSpeed"),
                    a.get("fastestSplit_1000"),
                    a.get("fastestSplit_1609"),
                    a.get("avgLeftBalance"),
                    a.get("trainingStressScore"),
                    a.get("max20MinPower"),
                ],
            )
            count += 1
    except Exception as e:
        logger.warning("Failed to sync activities: %s", e)
    return count


def _sync_garmin_sleep(garmin, db, dates):
    """Sync sleep data from Garmin for a list of date strings."""
    count = 0
    for d in dates:
        try:
            raw = _garmin_api_call(garmin.get_sleep_data, d)
            if not raw:
                continue
            sleep = raw.get("dailySleepDTO", {})
            if not sleep:
                continue
            scores = sleep.get("sleepScores", {})
            overall = scores.get("overall", {}) if scores else {}
            spo2_dto = raw.get("wellnessSpO2SleepSummaryDTO", {})
            db.execute(
                "INSERT OR REPLACE INTO garmin_sleep VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    d,
                    sleep.get("sleepTimeSeconds"),
                    sleep.get("deepSleepSeconds"),
                    sleep.get("lightSleepSeconds"),
                    sleep.get("remSleepSeconds"),
                    sleep.get("awakeSleepSeconds"),
                    overall.get("value") if overall else None,
                    overall.get("qualifierKey") if overall else None,
                    spo2_dto.get("averageSpo2") if spo2_dto else None,
                    raw.get("avgOvernightHrv"),
                    sleep.get("avgSleepStress"),
                    sleep.get("restingHeartRate"),
                ],
            )
            count += 1
        except Exception as e:
            logger.warning("Failed to sync sleep for %s: %s", d, e)
        time.sleep(0.3)
    return count


def _sync_garmin_training(garmin, db, dates):
    """Sync training status from Garmin for a list of date strings."""
    count = 0
    for d in dates:
        try:
            raw = _garmin_api_call(garmin.get_training_status, d)
            if not raw:
                continue

            # Navigate the deeply nested response
            mrt = raw.get("mostRecentTrainingStatus", raw)
            ltd = mrt.get("latestTrainingStatusData", {})
            # Get first device's data
            device_data = {}
            if ltd:
                first_key = next(iter(ltd), None)
                device_data = ltd.get(first_key, {}) if first_key else {}

            atl = device_data.get("acuteTrainingLoadDTO", {})
            vo2_section = raw.get("mostRecentVO2Max", mrt.get("mostRecentVO2Max", {}))
            vo2_generic = vo2_section.get("generic", {}) if vo2_section else {}

            vo2max_val = vo2_generic.get("vo2MaxPreciseValue") or vo2_generic.get("vo2MaxValue")
            vo2max_cycling = None

            # Try get_max_metrics for sport-specific VO2max
            try:
                max_metrics = _garmin_api_call(garmin.get_max_metrics, d)
                if max_metrics and isinstance(max_metrics, list):
                    for mm in max_metrics:
                        meta = mm.get("maxMetricValue", mm)
                        sport = (meta.get("sport") or mm.get("sport") or "").upper()
                        if sport == "CYCLING":
                            vo2max_cycling = meta.get("vo2MaxPreciseValue") or meta.get("vo2MaxValue")
                        elif sport == "RUNNING" and meta.get("vo2MaxPreciseValue"):
                            vo2max_val = meta.get("vo2MaxPreciseValue")
            except Exception:
                pass  # get_max_metrics may not be available

            # Try lactate threshold for FTP
            ftp_watts = None
            ftp_sport = None
            lthr_running = None
            lthr_cycling = None
            try:
                lt = _garmin_api_call(garmin.get_lactate_threshold)
                if lt:
                    power = lt.get("power", {})
                    shr = lt.get("speed_and_heart_rate", lt.get("speedAndHeartRate", {}))
                    if power:
                        ftp_watts = power.get("functionalThresholdPower")
                        ftp_sport = power.get("sport")
                    if shr:
                        lthr_running = shr.get("heartRate")
                        lthr_cycling = shr.get("heartRateCycling")
            except Exception:
                pass

            db.execute(
                "INSERT OR REPLACE INTO garmin_training_status VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    d,
                    device_data.get("trainingStatus"),
                    device_data.get("trainingStatusFeedbackPhrase"),
                    vo2max_val,
                    vo2max_cycling,
                    atl.get("dailyTrainingLoadAcute"),
                    atl.get("dailyTrainingLoadChronic"),
                    atl.get("dailyAcuteChronicWorkloadRatio"),
                    None,  # recovery_time_hrs — not reliably available
                    ftp_watts,
                    ftp_sport,
                    lthr_running,
                    lthr_cycling,
                ],
            )
            count += 1
        except Exception as e:
            logger.warning("Failed to sync training for %s: %s", d, e)
        time.sleep(0.3)
    return count


def _sync_garmin_body_fat(garmin, db, start_date, end_date):
    """Sync body fat % from Garmin for a date range."""
    count = 0
    try:
        raw = _garmin_api_call(garmin.get_body_composition, start_date, end_date)
        if not raw:
            return 0
        weights = raw.get("dateWeightList", raw.get("allWeightMetrics", []))
        if not weights:
            return 0
        for entry in weights:
            bf = entry.get("bodyFat")
            if bf is None:
                continue
            cal_date = entry.get("calendarDate")
            if not cal_date:
                # Try to parse from samplePk or timestamp
                ts = entry.get("date", entry.get("measurementTimeStamp"))
                if ts and isinstance(ts, (int, float)):
                    cal_date = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            if not cal_date:
                continue
            db.execute(
                "INSERT OR REPLACE INTO garmin_body_fat VALUES (?,?)",
                [cal_date, bf],
            )
            count += 1
    except Exception as e:
        logger.warning("Failed to sync body fat: %s", e)
    return count
