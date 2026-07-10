**Am I recovered? Did my fueling work? What should change next week?**
The only MCP combining MacroFactor nutrition with Garmin training data.

MacroFactor MCP is a local Fueling & Recovery Decision Engine. Give your MCP
client a MacroFactor export, optionally add Garmin, and ask for a decision
instead of manually comparing nutrition, sleep, body, and training screens.

## Quickstart

Install [`uv`](https://docs.astral.sh/uv/), then confirm the packaged server starts:

```bash
uvx macrofactor-mcp
```

Export your data from MacroFactor, put the `.xlsx` file in one local directory,
and add this to your MCP client configuration. Use absolute paths.

```json
{
  "mcpServers": {
    "macrofactor": {
      "command": "uvx",
      "args": ["macrofactor-mcp"],
      "env": {
        "MACROFACTOR_DATA_DIR": "/absolute/path/to/macrofactor-exports",
        "MACROFACTOR_DB_PATH": "/absolute/path/to/macrofactor.duckdb"
      }
    }
  }
}
```

Restart the MCP client. The server imports new workbooks on startup. Run
`setup_check`, then ask one of the questions below. If a desktop client cannot
find `uvx`, replace `"uvx"` with the absolute path returned by `which uvx`.

## Four flagship workflows

All examples below use synthetic data.

### `recovery_check` — Am I recovered?

Combines last night's sleep, HRV, body battery, training load, and yesterday's
fueling into a readiness view.

```text
Recovery Check: 2026-07-08
Sleep: 7h 38m, score 82; HRV +4 vs 7-day average
Training load: ACWR 1.08 (optimal)
Yesterday: 96% calorie target; 101% protein target
Overall: All systems go — 4 positive signals.
```

### `daily_briefing` — What do today's signals say together?

Shows the day as one picture instead of separate nutrition and training logs.

```text
Daily Briefing: 2026-07-08
Intake: 2,340 kcal (P 171g / F 72g / C 248g)
Adherence: 98% calories | 101% protein
Body Battery: 28 → 83 | Sleep: 7h 38m, score 82
Training: Productive | Load 612/568 (ACWR 1.08)
```

### `weekly_report` — What should change next week?

Reviews seven days of nutrition, weight, workouts, sleep, and training load.

```text
Weekly Report: 2026-07-02 to 2026-07-08
Average: 2,315 kcal | 168g protein
On target: calories 6/7 days | protein 7/7 days
Training: 4 sessions | Sleep: 7h 26m average
Next-week signal: fueling was consistent; protect sleep before adding load.
```

### `nutrition_performance_correlation` — Did my fueling work?

Compares nutrition with next-day Garmin recovery signals. It reports sample
sizes so small or missing datasets stay visible.

```text
Nutrition → Performance Correlation: 28 paired days
Protein >=90% target: next-day HRV 51 vs 45 (+6)
Protein >=90% target: sleep score 81 vs 76 (+5)
Energy deficit days: body battery 68 vs 75 (-7)
Observation: higher-protein days tended to precede better recovery.
```

## MacroFactor-only or MacroFactor + Garmin

| Mode | Decisions available |
|---|---|
| MacroFactor only | Nutrition, weight, workouts, `daily_briefing`, and `weekly_report`. Recovery output uses the nutrition signals that exist. |
| MacroFactor + Garmin | All four flagship workflows, including sleep, HRV, body battery, activities, and training load. |

Garmin is optional. To enable it, point `GARMINTOKENS` at an existing local
Garth/Garmin token directory, then run `sync_garmin`.

## Platform support

- macOS and Linux
- Windows is untested
- Python 3.10 or newer

## Limitations

- v0.1 requires a manual MacroFactor export; it does not connect to a MacroFactor API.
- Garmin correlation workflows need overlapping MacroFactor and Garmin dates.
- Correlations are observational, not proof that one behavior caused another outcome.
- Import support follows the current MacroFactor Quick Export and All-Time Data workbook layouts.

## Privacy

Raw nutrition, body, workout, and Garmin data stays in the local DuckDB
database. The server does not upload that database. However, **MCP tool results
are sent to whatever model or client the server is connected to**. Review that
client's data policy and avoid requesting more detail than you want to share.

## Disclaimer

This is an unofficial project. It is not affiliated with, endorsed by, or
supported by MacroFactor or Stronger By Science. It is not medical advice and
must not replace care from a qualified health professional.

Released under the [MIT License](LICENSE).

mcp-name: io.github.NasserAlbusaidi/macrofactor-mcp
