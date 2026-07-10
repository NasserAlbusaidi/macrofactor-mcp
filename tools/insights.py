"""
MacroFactor MCP Server — Insights Tool

Thin wrapper exposing the insights engine as an MCP tool.
"""

from main import mcp
from lib.insights import generate_insights


@mcp.tool()
def get_insights() -> str:
    """Get personalized insights based on your nutrition, training, sleep, and recovery data.

    Analyzes cross-domain patterns specific to YOUR data. Includes:
    - Current state (phase, weight trajectory, weekly nutrition pacing)
    - Trend alerts (sleep, HRV, body battery, compliance changes)
    - Personal correlations (what actually affects YOUR recovery)
    - Actionable recommendations prioritized by urgency
    - Sparkline trends for key metrics

    Works best with both MacroFactor and Garmin data synced.
    No arguments needed — analyzes everything automatically.
    """
    return generate_insights()
