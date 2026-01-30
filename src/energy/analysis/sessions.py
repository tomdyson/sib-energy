"""Sauna session detection from temperature data."""

from datetime import datetime
from pathlib import Path

from ..db import get_connection
from ..models import SaunaSession, TemperatureReading

# Session detection thresholds
HEATING_START_THRESHOLD = 35  # °C - heating has started
HOT_THRESHOLD = 55  # °C - sauna is "hot" and in active use
COOLING_THRESHOLD = 40  # °C - below this temp for sustained period = session ended
MIN_PEAK_TEMP = 55  # °C - minimum peak to count as a valid session
MIN_SESSION_DURATION = 30  # minutes
SESSION_GAP_MINUTES = 120  # if temp is below cooling threshold for this long, session ended


def detect_sessions(readings: list[TemperatureReading]) -> list[SaunaSession]:
    """Detect sauna sessions from a list of temperature readings.

    Algorithm:
    1. Session starts when temperature rises above HEATING_START_THRESHOLD
    2. Track peak temperature and active usage
    3. Session ends when temp is below COOLING_THRESHOLD for SESSION_GAP_MINUTES,
       OR when there's a large time gap between readings at low temperature
    """
    if not readings:
        return []

    sessions = []
    session_start = None
    session_last_hot = None  # Last time we were at HOT_THRESHOLD
    peak_temp = 0.0

    for i, reading in enumerate(readings):
        temp = reading.temperature_c

        # Calculate time gap from previous reading
        time_gap_minutes = 0
        if i > 0:
            time_gap_minutes = (reading.timestamp - readings[i - 1].timestamp).total_seconds() / 60

        if session_start is None:
            # Not in a session - look for heating start
            if temp >= HEATING_START_THRESHOLD:
                session_start = reading.timestamp
                peak_temp = temp
                if temp >= HOT_THRESHOLD:
                    session_last_hot = reading.timestamp
        else:
            # In a session
            if temp > peak_temp:
                peak_temp = temp

            if temp >= HOT_THRESHOLD:
                session_last_hot = reading.timestamp

            # Check if session should end:
            # 1. Large time gap at low temperature
            # 2. Been below cooling threshold for too long
            should_end = False
            end_time = reading.timestamp

            if temp < COOLING_THRESHOLD:
                if time_gap_minutes >= SESSION_GAP_MINUTES:
                    # Big gap at low temp - session ended at previous reading
                    should_end = True
                    if i > 0:
                        end_time = readings[i - 1].timestamp
                elif session_last_hot:
                    # Check if we've been cooling for too long
                    cooling_minutes = (reading.timestamp - session_last_hot).total_seconds() / 60
                    if cooling_minutes >= SESSION_GAP_MINUTES:
                        should_end = True

            if should_end and peak_temp >= MIN_PEAK_TEMP:
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
                session_start = None
                session_last_hot = None
                peak_temp = 0.0

                # If current reading is warm, might be start of new session
                if temp >= HEATING_START_THRESHOLD:
                    session_start = reading.timestamp
                    peak_temp = temp

    # Handle session in progress at end of data
    if session_start is not None and peak_temp >= MIN_PEAK_TEMP:
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

        readings = [
            TemperatureReading(
                sensor_id=row["sensor_id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                temperature_c=row["temperature_c"],
            )
            for row in rows
        ]

    return detect_sessions(readings)


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
