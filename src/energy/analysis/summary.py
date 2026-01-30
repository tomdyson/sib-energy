"""Generate LLM-friendly summaries of energy data."""

from datetime import datetime, time, timedelta
from pathlib import Path

from ..db import get_connection
from .sessions import get_sessions_for_period


def get_daily_summary(date: datetime, db_path: Path | None = None) -> dict:
    """Generate a summary for a specific day."""
    start = datetime.combine(date.date(), time.min)
    end = datetime.combine(date.date(), time.max)

    with get_connection(db_path) as conn:
        # Get electricity readings for the day
        elec_rows = conn.execute(
            """SELECT interval_start, interval_end, consumption_kwh, cost_pence
               FROM electricity_readings
               WHERE interval_start >= ? AND interval_start <= ?
               ORDER BY interval_start""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()

        # Calculate totals
        total_kwh = sum(row["consumption_kwh"] for row in elec_rows)
        total_cost = sum(row["cost_pence"] or 0 for row in elec_rows)

        # Calculate cheap-rate usage (midnight to 7am)
        cheap_kwh = 0.0
        cheap_start = time(0, 0)
        cheap_end = time(7, 0)
        for row in elec_rows:
            interval_time = datetime.fromisoformat(row["interval_start"]).time()
            if cheap_start <= interval_time < cheap_end:
                cheap_kwh += row["consumption_kwh"]

        # Find peak half-hour
        peak_reading = None
        peak_kwh = 0.0
        for row in elec_rows:
            if row["consumption_kwh"] > peak_kwh:
                peak_kwh = row["consumption_kwh"]
                peak_reading = row

        # Get sauna sessions for the day
        sessions = get_sessions_for_period(start, end, db_path)

    return {
        "date": date.date().isoformat(),
        "total_kwh": round(total_kwh, 2),
        "total_cost_pence": round(total_cost, 1),
        "total_cost_pounds": round(total_cost / 100, 2),
        "cheap_rate_kwh": round(cheap_kwh, 2),
        "cheap_rate_percent": round(cheap_kwh / total_kwh * 100, 1) if total_kwh > 0 else 0,
        "peak_half_hour": {
            "time": peak_reading["interval_start"] if peak_reading else None,
            "kwh": round(peak_kwh, 2),
        },
        "readings_count": len(elec_rows),
        "sauna_sessions": [
            {
                "start": s.start_time.strftime("%H:%M"),
                "end": s.end_time.strftime("%H:%M"),
                "duration_minutes": s.duration_minutes,
                "peak_temp_c": s.peak_temperature_c,
            }
            for s in sessions
        ],
    }


def get_period_summary(
    start: datetime, end: datetime, db_path: Path | None = None
) -> dict:
    """Generate a summary for a date range."""
    with get_connection(db_path) as conn:
        # Get total house electricity (EON smart meter)
        elec_row = conn.execute(
            """SELECT
                   SUM(consumption_kwh) as total_kwh,
                   SUM(cost_pence) as total_cost,
                   COUNT(*) as count,
                   MIN(interval_start) as earliest,
                   MAX(interval_start) as latest
               FROM electricity_readings
               WHERE source = 'eon' AND interval_start >= ? AND interval_start <= ?""",
            (start.isoformat(), end.isoformat()),
        ).fetchone()

        # Get studio circuit (Shelly - subset of total)
        studio_row = conn.execute(
            """SELECT
                   SUM(consumption_kwh) as total_kwh,
                   SUM(cost_pence) as total_cost,
                   COUNT(*) as count
               FROM electricity_readings
               WHERE source = 'shelly_studio_phase' AND interval_start >= ? AND interval_start <= ?""",
            (start.isoformat(), end.isoformat()),
        ).fetchone()

        # Daily breakdown (EON only for totals)
        daily_rows = conn.execute(
            """SELECT
                   DATE(interval_start) as day,
                   SUM(consumption_kwh) as kwh,
                   SUM(cost_pence) as cost
               FROM electricity_readings
               WHERE source = 'eon' AND interval_start >= ? AND interval_start <= ?
               GROUP BY DATE(interval_start)
               ORDER BY day""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()

        # Studio daily breakdown
        studio_daily_rows = conn.execute(
            """SELECT
                   DATE(interval_start) as day,
                   SUM(consumption_kwh) as kwh
               FROM electricity_readings
               WHERE source = 'shelly_studio_phase' AND interval_start >= ? AND interval_start <= ?
               GROUP BY DATE(interval_start)
               ORDER BY day""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()

        # Get sauna sessions
        sessions = get_sessions_for_period(start, end, db_path)

    days_count = len(daily_rows)
    total_kwh = elec_row["total_kwh"] or 0
    total_cost = elec_row["total_cost"] or 0
    studio_kwh = studio_row["total_kwh"] or 0
    studio_cost = studio_row["total_cost"] or 0

    # Calculate studio as % of total
    studio_percent = round(studio_kwh / total_kwh * 100, 1) if total_kwh > 0 else 0

    # Build studio daily map for comparison
    studio_daily_map = {row["day"]: row["kwh"] for row in studio_daily_rows}

    return {
        "period": {
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "days": days_count,
        },
        "totals": {
            "kwh": round(total_kwh, 2),
            "cost_pence": round(total_cost, 1),
            "cost_pounds": round(total_cost / 100, 2),
        },
        "averages": {
            "daily_kwh": round(total_kwh / days_count, 2) if days_count > 0 else 0,
            "daily_cost_pounds": round(total_cost / 100 / days_count, 2) if days_count > 0 else 0,
        },
        "studio": {
            "kwh": round(studio_kwh, 2),
            "cost_pounds": round(studio_cost / 100, 2) if studio_cost else 0,
            "percent_of_total": studio_percent,
            "daily_breakdown": [
                {
                    "date": row["day"],
                    "studio_kwh": round(studio_daily_map.get(row["day"], 0), 2),
                    "total_kwh": round(row["kwh"], 2),
                    "studio_percent": round(studio_daily_map.get(row["day"], 0) / row["kwh"] * 100, 1) if row["kwh"] > 0 else 0,
                }
                for row in daily_rows
            ],
        },
        "daily_breakdown": [
            {"date": row["day"], "kwh": round(row["kwh"], 2), "cost_pounds": round((row["cost"] or 0) / 100, 2)}
            for row in daily_rows
        ],
        "sauna": {
            "session_count": len(sessions),
            "total_duration_minutes": sum(s.duration_minutes for s in sessions),
            "sessions": [
                {
                    "date": s.start_time.date().isoformat(),
                    "start": s.start_time.strftime("%H:%M"),
                    "duration_minutes": s.duration_minutes,
                    "peak_temp_c": s.peak_temperature_c,
                }
                for s in sessions
            ],
        },
    }


def format_daily_summary_text(summary: dict) -> str:
    """Format a daily summary as human-readable text."""
    lines = [
        f"Daily Summary for {summary['date']}",
        f"- Total consumption: {summary['total_kwh']} kWh",
        f"- Estimated cost: £{summary['total_cost_pounds']:.2f}",
        f"- Cheap-rate usage: {summary['cheap_rate_kwh']} kWh ({summary['cheap_rate_percent']}%)",
    ]

    if summary["peak_half_hour"]["time"]:
        peak_time = datetime.fromisoformat(summary["peak_half_hour"]["time"]).strftime("%H:%M")
        lines.append(f"- Peak half-hour: {peak_time} ({summary['peak_half_hour']['kwh']} kWh)")

    if summary["sauna_sessions"]:
        for session in summary["sauna_sessions"]:
            lines.append(
                f"- Sauna session: {session['start']}-{session['end']}, "
                f"peak {session['peak_temp_c']}°C"
            )
    else:
        lines.append("- Sauna: No sessions")

    return "\n".join(lines)


def format_period_summary_text(summary: dict) -> str:
    """Format a period summary as human-readable text."""
    lines = [
        f"Energy Summary: {summary['period']['start']} to {summary['period']['end']}",
        f"({summary['period']['days']} days)",
        "",
        "Totals:",
        f"  - Consumption: {summary['totals']['kwh']} kWh",
        f"  - Cost: £{summary['totals']['cost_pounds']:.2f}",
        "",
        "Daily Averages:",
        f"  - Consumption: {summary['averages']['daily_kwh']} kWh/day",
        f"  - Cost: £{summary['averages']['daily_cost_pounds']:.2f}/day",
    ]

    # Studio breakdown (if data available)
    if summary.get("studio", {}).get("kwh", 0) > 0:
        lines.extend([
            "",
            "Studio:",
            f"  - Consumption: {summary['studio']['kwh']} kWh ({summary['studio']['percent_of_total']}% of total)",
            f"  - Cost: £{summary['studio']['cost_pounds']:.2f}",
        ])

    if summary["sauna"]["session_count"] > 0:
        lines.extend([
            "",
            "Sauna:",
            f"  - Sessions: {summary['sauna']['session_count']}",
            f"  - Total time: {summary['sauna']['total_duration_minutes']} minutes",
        ])

    return "\n".join(lines)
