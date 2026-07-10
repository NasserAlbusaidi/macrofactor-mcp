"""
MacroFactor MCP Server — Nutrition Tools

Tools for viewing nutrition summaries, date ranges, food logs, and food search.
"""

from datetime import date

from main import mcp, get_db


@mcp.tool()
def get_nutrition_summary(date_str: str = "") -> str:
    """Get nutrition summary for a specific date.

    Args:
        date_str: Date in YYYY-MM-DD format. Empty = today.
    """
    if not date_str:
        date_str = date.today().strftime("%Y-%m-%d")

    with get_db(read_only=True) as db:
        row = db.execute(
            """SELECT date, calories_kcal, protein_g, fat_g, carbs_g,
                      target_calories, target_protein, target_fat, target_carbs,
                      expenditure, weight_kg, trend_weight_kg, steps,
                      fiber_g, sodium_mg, sugars_g, alcohol_g, water_g, fat_percent
               FROM daily WHERE date = ?""",
            [date_str],
        ).fetchone()

    if not row:
        return f"No data for {date_str}."

    (d, cal, pro, fat, carb, tcal, tpro, tfat, tcarb,
     exp, wt, twt, steps, fiber, sodium, sugar, alc, water, fat_pct) = row

    def pct(actual, target):
        if actual and target and target > 0:
            return f" ({actual / target * 100:.0f}%)"
        return ""

    lines = [
        f"Nutrition Summary — {d}", "",
        f"Calories: {cal or 0:.0f} / {tcal or 0:.0f} kcal{pct(cal, tcal)}",
        f"Protein:  {pro or 0:.0f} / {tpro or 0:.0f} g{pct(pro, tpro)}",
        f"Fat:      {fat or 0:.0f} / {tfat or 0:.0f} g{pct(fat, tfat)}",
        f"Carbs:    {carb or 0:.0f} / {tcarb or 0:.0f} g{pct(carb, tcarb)}",
        "",
        f"Expenditure: {exp or 0:.0f} kcal",
        f"Deficit/Surplus: {(cal or 0) - (exp or 0):+.0f} kcal",
    ]
    if wt:
        lines.append(f"Scale Weight: {wt:.2f} kg")
    if twt:
        lines.append(f"Trend Weight: {twt:.2f} kg")
    if fat_pct:
        lines.append(f"Body Fat: {fat_pct:.1f}%")
    if steps:
        lines.append(f"Steps: {steps:,}")

    extras = []
    if fiber: extras.append(f"Fiber: {fiber:.0f}g")
    if sugar: extras.append(f"Sugar: {sugar:.0f}g")
    if alc: extras.append(f"Alcohol: {alc:.0f}g")
    if water: extras.append(f"Water: {water:.0f}g")
    if extras:
        lines.append("  ".join(extras))

    return "\n".join(lines)


@mcp.tool()
def get_nutrition_range(start_date: str, end_date: str) -> str:
    """Get daily nutrition data for a date range.

    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
    """
    with get_db(read_only=True) as db:
        rows = db.execute(
            """SELECT date, calories_kcal, protein_g, fat_g, carbs_g,
                      target_calories, expenditure, weight_kg, steps
               FROM daily WHERE date BETWEEN ? AND ? ORDER BY date""",
            [start_date, end_date],
        ).fetchall()

    if not rows:
        return f"No data between {start_date} and {end_date}."

    lines = [f"Nutrition: {start_date} to {end_date} ({len(rows)} days)\n"]
    lines.append(
        f"{'Date':<12} {'Cal':>6} {'Pro':>5} {'Fat':>5} "
        f"{'Carb':>5} {'Target':>7} {'Delta':>6} {'Wt':>6} {'Steps':>7}"
    )
    lines.append("-" * 72)

    total_cal = total_pro = total_fat = total_carb = total_exp = 0
    for d, cal, pro, fat, carb, tcal, exp, wt, steps in rows:
        cal, pro, fat, carb, exp = cal or 0, pro or 0, fat or 0, carb or 0, exp or 0
        total_cal += cal; total_pro += pro; total_fat += fat
        total_carb += carb; total_exp += exp
        wt_str = f"{wt:.1f}" if wt else "  -"
        st_str = f"{steps:,}" if steps else "  -"
        lines.append(
            f"{str(d):<12} {cal:>6.0f} {pro:>5.0f} {fat:>5.0f} "
            f"{carb:>5.0f} {tcal or 0:>7.0f} {cal - exp:>+6.0f} "
            f"{wt_str:>6} {st_str:>7}"
        )

    n = len(rows)
    lines.append("-" * 72)
    lines.append(
        f"{'Average':<12} {total_cal/n:>6.0f} {total_pro/n:>5.0f} "
        f"{total_fat/n:>5.0f} {total_carb/n:>5.0f} "
        f"{'':>7} {(total_cal-total_exp)/n:>+6.0f}"
    )
    lines.append(
        f"{'Total':<12} {total_cal:>6.0f} {total_pro:>5.0f} "
        f"{total_fat:>5.0f} {total_carb:>5.0f}"
    )
    return "\n".join(lines)


@mcp.tool()
def get_food_log(date_str: str = "") -> str:
    """Get all foods logged on a specific date.

    Args:
        date_str: Date (YYYY-MM-DD). Default: today.
    """
    if not date_str:
        date_str = date.today().strftime("%Y-%m-%d")

    with get_db(read_only=True) as db:
        rows = db.execute(
            """SELECT time, food_name, calories_kcal, protein_g, fat_g,
                      carbs_g, serving_weight_g
               FROM food_log WHERE date = ? ORDER BY time""",
            [date_str],
        ).fetchall()

        if not rows:
            note = db.execute(
                "SELECT note FROM food_log_notes WHERE date = ?", [date_str]
            ).fetchone()
            msg = f"No detailed food log for {date_str}."
            if note:
                msg += f"\nNote: {note[0]}"
            msg += " (Detailed food log requires Quick Export format.)"
            return msg

    lines = [f"Food Log — {date_str} ({len(rows)} items)\n"]
    total_cal = total_pro = total_fat = total_carb = 0
    for t, name, cal, pro, fat, carb, wt in rows:
        cal, pro, fat, carb = cal or 0, pro or 0, fat or 0, carb or 0
        total_cal += cal; total_pro += pro; total_fat += fat; total_carb += carb
        wt_str = f" ({wt:.0f}g)" if wt else ""
        lines.append(f"  {t or '??:??'}  {name}{wt_str}")
        lines.append(f"          {cal:.0f} kcal | P {pro:.0f}g | F {fat:.0f}g | C {carb:.0f}g")

    lines.append(
        f"\n  Total: {total_cal:.0f} kcal | P {total_pro:.0f}g "
        f"| F {total_fat:.0f}g | C {total_carb:.0f}g"
    )
    return "\n".join(lines)


@mcp.tool()
def search_food(query: str, limit: int = 20) -> str:
    """Search logged foods by name across all dates.

    Also searches custom foods if no log results found.

    Args:
        query: Search term (case-insensitive).
        limit: Max results (default 20).
    """
    with get_db(read_only=True) as db:
        rows = db.execute(
            """SELECT date, food_name, calories_kcal, protein_g, fat_g,
                      carbs_g, serving_weight_g
               FROM food_log WHERE lower(food_name) LIKE ?
               ORDER BY date DESC LIMIT ?""",
            [f"%{query.lower()}%", limit],
        ).fetchall()

        if rows:
            lines = [f"Foods matching '{query}' ({len(rows)} log results)\n"]
            for d, name, cal, pro, fat, carb, wt in rows:
                wt_str = f" ({wt:.0f}g)" if wt else ""
                lines.append(
                    f"  {d}  {name}{wt_str}  —  {cal or 0:.0f} kcal "
                    f"| P {pro or 0:.0f}g | F {fat or 0:.0f}g | C {carb or 0:.0f}g"
                )
            return "\n".join(lines)

        cf_rows = db.execute(
            """SELECT food_name, calories_kcal, protein_g, fat_g, carbs_g,
                      serving_weight_g, serving_size
               FROM custom_foods WHERE lower(food_name) LIKE ? LIMIT ?""",
            [f"%{query.lower()}%", limit],
        ).fetchall()

    if cf_rows:
        lines = [f"Custom foods matching '{query}' ({len(cf_rows)} results)\n"]
        for name, cal, pro, fat, carb, wt, srv in cf_rows:
            srv_str = f" [{srv}]" if srv else ""
            wt_str = f" ({wt:.0f}g)" if wt else ""
            lines.append(
                f"  {name}{srv_str}{wt_str}  —  {cal or 0:.0f} kcal "
                f"| P {pro or 0:.0f}g | F {fat or 0:.0f}g | C {carb or 0:.0f}g"
            )
        return "\n".join(lines)

    return f"No foods matching '{query}'."
