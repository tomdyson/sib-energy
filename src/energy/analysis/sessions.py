"""Sauna session detection from temperature data."""

from datetime import datetime
from pathlib import Path

from ..db import get_connection
from ..models import SaunaSession, TemperatureReading

# Session detection thresholds
# Session detection thresholds
STARTUP_DELTA_OVER_OUTDOOR = 5.0  # °C increase over outdoor temp to trigger potential session
HEATING_START_THRESHOLD = 28  # °C - fallback if no outdoor info
HOT_THRESHOLD = 60  # °C - sauna is "hot" and in active use
MIN_PEAK_TEMP = 65  # °C - minimum peak to count as a valid session
MIN_SESSION_DURATION = 30  # minutes

# Fallback/Emergency end threshold if HOT_THRESHOLD logic fails
COOLING_THRESHOLD = 40  # °C
SESSION_GAP_MINUTES = 120


def detect_sessions(
    readings: list[TemperatureReading],
    outside_readings: list[TemperatureReading] | None = None,
) -> list[SaunaSession]:
    """Detect sauna sessions from a list of temperature readings.

    Algorithm:
    1. Session starts when temperature rises above (outdoor_temp + delta)
       AND the next reading is an increase (trend check).
    2. Track peak temperature.
    3. Session ends when temp drops below HOT_THRESHOLD after hitting MIN_PEAK_TEMP.
    """
    if not readings:
        return []

    sessions = []
    session_start = None
    peak_temp = 0.0
    hit_peak = False

    # Create a lookup for outdoor readings if present
    outdoor_lookup = {}
    if outside_readings:
        # Sort for safety and use for "closest preceding" lookup
        sorted_outside = sorted(outside_readings, key=lambda x: x.timestamp)
        # We index by hour to make lookup efficient
        for r in sorted_outside:
            # Handle potentially mixed aware/naive datetimes by normalizing to naive
            hour_ts = r.timestamp.replace(minute=0, second=0, microsecond=0, tzinfo=None)
            outdoor_lookup[hour_ts] = r.temperature_c

    def get_outdoor_temp(ts: datetime) -> float:
        # Handle potentially mixed aware/naive datetimes by normalizing to naive
        ts_naive = ts.replace(tzinfo=None)
        hour_ts = ts_naive.replace(minute=0, second=0, microsecond=0)
        # Try exact hour, then preceding hour, then fallback
        if hour_ts in outdoor_lookup:
            return outdoor_lookup[hour_ts]
        # Fallback to HEATING_START_THRESHOLD - STARTUP_DELTA_OVER_OUTDOOR
        return HEATING_START_THRESHOLD - STARTUP_DELTA_OVER_OUTDOOR

    for i, reading in enumerate(readings):
        temp = reading.temperature_c

        if session_start is None:
            # Not in a session - look for heating start
            outdoor_temp = get_outdoor_temp(reading.timestamp)
            start_threshold = outdoor_temp + STARTUP_DELTA_OVER_OUTDOOR

            # Need at least one more reading to check trend
            if i < len(readings) - 1:
                next_temp = readings[i + 1].temperature_c
                if temp > start_threshold and next_temp > temp:
                    session_start = reading.timestamp
                    peak_temp = temp
                    hit_peak = False
        else:
            # In a session
            if temp > peak_temp:
                peak_temp = temp

            if peak_temp >= MIN_PEAK_TEMP:
                hit_peak = True

            # Check if session should end:
            # Drop below HOT_THRESHOLD after reaching peak
            should_end = False
            if hit_peak and temp < HOT_THRESHOLD:
                should_end = True

            # Fallback end: extreme cooling or time gap (handling edge cases)
            if not should_end:
                if temp < COOLING_THRESHOLD:
                    # Check gap from previous reading
                    if i > 0:
                        gap_mins = (reading.timestamp - readings[i - 1].timestamp).total_seconds() / 60
                        if gap_mins >= SESSION_GAP_MINUTES:
                            should_end = True

            if should_end:
                end_time = reading.timestamp
                duration = int((end_time - session_start).total_seconds() / 60)
                if hit_peak and duration >= MIN_SESSION_DURATION:
                    sessions.append(
                        SaunaSession(
                            start_time=session_start,
                            end_time=end_time,
                            duration_minutes=duration,
                            peak_temperature_c=peak_temp,
                        )
                    )
                session_start = None
                peak_temp = 0.0
                hit_peak = False

    # Handle session in progress at end of data
    if session_start is not None and hit_peak:
        end_time = readings[-1].timestamp
        duration = int((end_time - session_start).total_seconds() / 60)
        if duration >= MIN_SESSION_DURATION:
            sessions.append(
                SaunaSession(
                    start_time=session_start,
                    end_time=end_time,
                    duration_minutes=duration,
                    peak_temperature_c=peak_temp,
                )
            )

    return sessions


def detect_sessions_from_db(
    start: datetime | None = None,
    end: datetime | None = None,
    db_path: Path | None = None,
) -> list[SaunaSession]:
    """Detect sauna sessions from database readings."""
    with get_connection(db_path) as conn:
        # Fetch sauna readings
        query = """SELECT sensor_id, timestamp, temperature_c
                   FROM temperature_readings
                   WHERE sensor_id = 'sauna'"""
        params = []

        if start:
            query += " AND timestamp >= ?"
            params.append(start.isoformat())
        if end:
            query += " AND timestamp <= ?"
            params.append(end.isoformat())

        query += " ORDER BY timestamp"
        rows = conn.execute(query, params).fetchall()

        sauna_readings = [
            TemperatureReading(
                sensor_id=row["sensor_id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                temperature_c=row["temperature_c"],
            )
            for row in rows
        ]

        # Fetch outside temperature readings for correlation
        query_out = """SELECT sensor_id, timestamp, temperature_c
                       FROM temperature_readings
                       WHERE sensor_id = 'outside_temperature'"""
        params_out = []
        if start:
            query_out += " AND timestamp >= ?"
            params_out.append(start.isoformat())
        if end:
            query_out += " AND timestamp <= ?"
            params_out.append(end.isoformat())

        query_out += " ORDER BY timestamp"
        rows_out = conn.execute(query_out, params_out).fetchall()
        outside_readings = [
            TemperatureReading(
                sensor_id=row["sensor_id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                temperature_c=row["temperature_c"],
            )
            for row in rows_out
        ]

    return detect_sessions(sauna_readings, outside_readings)


def save_sessions(sessions: list[SaunaSession], db_path: Path | None = None) -> dict:
    """Save detected sessions to database. Returns counts."""
    imported = 0
    skipped = 0

    with get_connection(db_path) as conn:
        for session in sessions:
            # Check if session already exists (same start time)
            existing = conn.execute(
                "SELECT id FROM sauna_sessions WHERE start_time = ?",
                (session.start_time.isoformat(),),
            ).fetchone()

            if existing:
                skipped += 1
                continue

            conn.execute(
                """INSERT INTO sauna_sessions
                   (start_time, end_time, duration_minutes, peak_temperature_c, estimated_kwh)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    session.start_time.isoformat(),
                    session.end_time.isoformat(),
                    session.duration_minutes,
                    session.peak_temperature_c,
                    session.estimated_kwh,
                ),
            )
            imported += 1

        conn.commit()

    return {"imported": imported, "skipped": skipped}


def refresh_sessions(db_path: Path | None = None) -> dict:
    """Re-detect and update all sessions from temperature data.

    Clears existing sessions and re-detects from scratch.
    """
    # Clear existing sessions
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM sauna_sessions")
        conn.commit()

    # Detect and save new sessions
    detected = detect_sessions_from_db(db_path=db_path)
    return save_sessions(detected, db_path)


def get_sessions_for_period(
    start: datetime, end: datetime, db_path: Path | None = None
) -> list[SaunaSession]:
    """Get all sessions within a time period."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT start_time, end_time, duration_minutes, peak_temperature_c, estimated_kwh
               FROM sauna_sessions
               WHERE start_time >= ? AND start_time <= ?
               ORDER BY start_time""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()

        return [
            SaunaSession(
                start_time=datetime.fromisoformat(row["start_time"]),
                end_time=datetime.fromisoformat(row["end_time"]),
                duration_minutes=row["duration_minutes"],
                peak_temperature_c=row["peak_temperature_c"],
                estimated_kwh=row["estimated_kwh"],
            )
            for row in rows
        ]
