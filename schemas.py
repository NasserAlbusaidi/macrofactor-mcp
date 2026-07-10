"""
MacroFactor + Garmin MCP Server — Database Schemas

All DuckDB table definitions and migration logic.
"""

import logging

logger = logging.getLogger("macrofactor-mcp")

# ═════════════════════════════════════════════════════════════════════════════
#  DATA LAYER — SCHEMAS
# ═════════════════════════════════════════════════════════════════════════════

ALL_SCHEMAS = [
    """CREATE TABLE IF NOT EXISTS daily (
        date              DATE PRIMARY KEY,
        expenditure       DOUBLE,
        trend_weight_kg   DOUBLE,
        weight_kg         DOUBLE,
        fat_percent       DOUBLE,
        calories_kcal     DOUBLE,
        protein_g         DOUBLE,
        fat_g             DOUBLE,
        carbs_g           DOUBLE,
        target_calories   DOUBLE,
        target_protein    DOUBLE,
        target_fat        DOUBLE,
        target_carbs      DOUBLE,
        steps             INTEGER,
        alcohol_g         DOUBLE,
        fiber_g           DOUBLE,
        sodium_mg         DOUBLE,
        sugars_g          DOUBLE,
        caffeine_mg       DOUBLE,
        calcium_mg        DOUBLE,
        iron_mg           DOUBLE,
        vitamin_c_mg      DOUBLE,
        vitamin_d_mcg     DOUBLE,
        water_g           DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS food_log (
        date              DATE,
        time              VARCHAR,
        food_name         VARCHAR,
        serving_size      VARCHAR,
        serving_qty       DOUBLE,
        serving_weight_g  DOUBLE,
        calories_kcal     DOUBLE,
        protein_g         DOUBLE,
        fat_g             DOUBLE,
        carbs_g           DOUBLE,
        fiber_g           DOUBLE,
        sodium_mg         DOUBLE,
        sugars_g          DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS workouts (
        date              DATE,
        duration_sec      INTEGER,
        workout_name      VARCHAR,
        exercise          VARCHAR,
        set_type          VARCHAR,
        weight_kg         DOUBLE,
        reps              INTEGER,
        rir               DOUBLE,
        duration          DOUBLE,
        distance_yd       DOUBLE,
        distance_mi       DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS muscle_sets (
        date              DATE,
        muscle_group      VARCHAR,
        sets              DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS muscle_volume (
        date              DATE,
        muscle_group      VARCHAR,
        volume_kg         DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS nutrition_targets (
        program_date      DATE,
        weekday_num       INTEGER,
        weekday_name      VARCHAR,
        calorie_target    DOUBLE,
        fat_target        DOUBLE,
        protein_target    DOUBLE,
        carb_target       DOUBLE,
        expenditure       DOUBLE,
        daily_average     DOUBLE,
        weight_kg         DOUBLE,
        calc_mode         VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS exercise_tracking (
        date              DATE,
        exercise          VARCHAR,
        metric            VARCHAR,
        value             DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS body_metrics (
        date              DATE,
        metric            VARCHAR,
        value             DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS custom_foods (
        food_name         VARCHAR,
        serving_size      VARCHAR,
        serving_qty       DOUBLE,
        serving_weight_g  DOUBLE,
        calories_kcal     DOUBLE,
        protein_g         DOUBLE,
        fat_g             DOUBLE,
        carbs_g           DOUBLE,
        fiber_g           DOUBLE,
        sodium_mg         DOUBLE,
        sugars_g          DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS food_log_notes (
        date              DATE,
        name              VARCHAR,
        note              VARCHAR
    )""",
    """CREATE TABLE IF NOT EXISTS import_log (
        filename          VARCHAR PRIMARY KEY,
        imported_at       TIMESTAMP DEFAULT current_timestamp,
        format            VARCHAR,
        rows_daily        INTEGER,
        rows_food         INTEGER,
        rows_workouts     INTEGER
    )""",
    # ── Garmin tables ──
    """CREATE TABLE IF NOT EXISTS garmin_daily_stats (
        date                   DATE PRIMARY KEY,
        total_steps            INTEGER,
        daily_step_goal        INTEGER,
        distance_meters        DOUBLE,
        floors_ascended        INTEGER,
        floors_descended       INTEGER,
        total_calories         INTEGER,
        active_calories        INTEGER,
        bmr_calories           INTEGER,
        moderate_intensity_min INTEGER,
        vigorous_intensity_min INTEGER,
        resting_hr             INTEGER,
        avg_stress             INTEGER,
        max_stress             INTEGER,
        body_battery_high      INTEGER,
        body_battery_low       INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS garmin_activities (
        activity_id                BIGINT PRIMARY KEY,
        date                       DATE,
        activity_name              VARCHAR,
        activity_type              VARCHAR,
        duration_sec               DOUBLE,
        distance_m                 DOUBLE,
        avg_hr                     INTEGER,
        max_hr                     INTEGER,
        calories                   INTEGER,
        avg_speed_mps              DOUBLE,
        max_speed_mps              DOUBLE,
        avg_power                  DOUBLE,
        max_power                  DOUBLE,
        normalized_power           DOUBLE,
        training_effect_aerobic    DOUBLE,
        training_effect_anaerobic  DOUBLE,
        elevation_gain             DOUBLE,
        vo2max                     DOUBLE,
        avg_cadence                DOUBLE,
        avg_ground_contact_time    DOUBLE,
        avg_stride_length          DOUBLE,
        avg_vertical_oscillation   DOUBLE,
        avg_vertical_ratio         DOUBLE,
        avg_grade_adjusted_speed   DOUBLE,
        fastest_split_1000         DOUBLE,
        fastest_split_1609         DOUBLE,
        avg_left_balance           DOUBLE,
        training_stress_score      DOUBLE,
        max_20min_power            DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS garmin_sleep (
        date                  DATE PRIMARY KEY,
        total_sleep_sec       INTEGER,
        deep_sleep_sec        INTEGER,
        light_sleep_sec       INTEGER,
        rem_sleep_sec         INTEGER,
        awake_sec             INTEGER,
        sleep_score           INTEGER,
        sleep_score_qualifier VARCHAR,
        avg_spo2              INTEGER,
        avg_overnight_hrv     INTEGER,
        avg_sleep_stress      DOUBLE,
        resting_hr            INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS garmin_training_status (
        date                     DATE PRIMARY KEY,
        training_status          VARCHAR,
        training_status_feedback VARCHAR,
        vo2max_running           DOUBLE,
        vo2max_cycling           DOUBLE,
        acute_load               DOUBLE,
        chronic_load             DOUBLE,
        load_ratio               DOUBLE,
        recovery_time_hrs        INTEGER,
        ftp_watts                INTEGER,
        ftp_sport                VARCHAR,
        lthr_running             INTEGER,
        lthr_cycling             INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS garmin_body_fat (
        date            DATE PRIMARY KEY,
        body_fat_pct    DOUBLE
    )""",
    """CREATE TABLE IF NOT EXISTS garmin_sync_log (
        synced_at         TIMESTAMP PRIMARY KEY DEFAULT current_timestamp,
        last_date_synced  DATE,
        stats_rows        INTEGER,
        activities_rows   INTEGER,
        sleep_rows        INTEGER,
        training_rows     INTEGER
    )""",
]


def init_db(db):
    """Create all tables if they don't exist, then run migrations."""
    for schema in ALL_SCHEMAS:
        db.execute(schema)
    _migrate_garmin_activities(db)


def _migrate_garmin_activities(db):
    """Add dynamics columns to garmin_activities if they don't exist yet."""
    new_columns = [
        ("elevation_gain", "DOUBLE"),
        ("vo2max", "DOUBLE"),
        ("avg_cadence", "DOUBLE"),
        ("avg_ground_contact_time", "DOUBLE"),
        ("avg_stride_length", "DOUBLE"),
        ("avg_vertical_oscillation", "DOUBLE"),
        ("avg_vertical_ratio", "DOUBLE"),
        ("avg_grade_adjusted_speed", "DOUBLE"),
        ("fastest_split_1000", "DOUBLE"),
        ("fastest_split_1609", "DOUBLE"),
        ("avg_left_balance", "DOUBLE"),
        ("training_stress_score", "DOUBLE"),
        ("max_20min_power", "DOUBLE"),
    ]
    for col_name, col_type in new_columns:
        try:
            db.execute(
                f"ALTER TABLE garmin_activities ADD COLUMN {col_name} {col_type}"
            )
        except Exception:
            pass  # Column already exists
