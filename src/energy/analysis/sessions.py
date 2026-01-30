"""Sauna session detection from temperature data."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..db import get_connection
from ..models import SaunaSession, TemperatureReading

# Session detection thresholds
STARTUP_DELTA_OVER_OUTDOOR = 5.0  # °C increase over outdoor temp to trigger potential session
HEATING_START_THRESHOLD = 28  # °C - fallback if no outdoor info
HOT_THRESHOLD = 60  # °C - sauna is "hot" and in active use
MIN_PEAK_TEMP = 65  # °C - minimum peak to count as a valid session
MIN_SESSION_DURATION = 30  # minutes

# Fallback/Emergency end threshold if HOT_THRESHOLD logic fails
COOLING_THRESHOLD = 40  # °C
SESSION_GAP_MINUTES = 120

# Electricity-based heating detection thresholds
# The 9kW sauna heater draws ~4.5 kWh per 30-min slot when actively heating
# We use 3.0 kWh as the threshold to account for some variation
HEATING_KWH_THRESHOLD = 3.0  # kWh per 30-min slot indicates active heating
HEATING_WINDOW_MINUTES = 180  # Look up to 3 hours from session start for heating

# Tariff rates (pence per kWh)
CHEAP_RATE = 7  # 00:00-07:00
PEAK_RATE = 25  # 07:00-24:00
CHEAP_HOUR_END = 7  # Cheap rate ends at 07:00


@dataclass
class SaunaHeatingAnalysis:
    """Detailed analysis of sauna heating from electricity data."""

    session_id: int | None
    start_time: datetime
    peak_temperature_c: float
    outside_temperature_c: float | None

    # Heating metrics from electricity data
    heating_minutes: int  # Actual active heating time
    total_kwh: float
    cheap_kwh: float  # kWh during cheap rate (00:00-07:00)
    peak_kwh: float  # kWh during peak rate (07:00-24:00)
    cost_pence: float

    # Slot breakdown
    cheap_slots: int
    peak_slots: int

    @property
    def cost_gbp(self) -> float:
        """Cost in GBP."""
        return self.cost_pence / 100


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

    Clears existing sessions, re-detects from temperature patterns,
    then correlates with electricity data to calculate estimated_kwh.
    """
    # Clear existing sessions
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM sauna_sessions")
        conn.commit()

    # Detect and save new sessions from temperature data
    detected = detect_sessions_from_db(db_path=db_path)
    result = save_sessions(detected, db_path)

    # Now correlate with electricity data to calculate estimated_kwh
    # This analyzes (EON - Studio) consumption to find actual heating periods
    kwh_result = update_session_estimated_kwh(db_path)
    result["kwh_updated"] = kwh_result["updated"]

    return result


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


def analyze_session_heating(
    session_start: datetime,
    peak_temperature_c: float,
    session_id: int | None = None,
    db_path: Path | None = None,
) -> SaunaHeatingAnalysis | None:
    """Analyze actual heating consumption for a sauna session using electricity data.

    The sauna is on the main house circuit (not the studio), so we calculate:
        main_house_kwh = EON_total - Studio_consumption

    Active heating is identified when main_house_kwh > HEATING_KWH_THRESHOLD (3.0 kWh)
    per 30-minute slot, which indicates the 9kW heater is running (~4.5 kWh/slot).

    Returns detailed breakdown of cheap vs peak rate consumption.
    """
    with get_connection(db_path) as conn:
        # Get electricity readings for the heating window
        # Start slightly before session start and extend up to 3 hours
        window_start = session_start - timedelta(minutes=30)
        window_end = session_start + timedelta(minutes=HEATING_WINDOW_MINUTES)

        # Query to get main house consumption (EON - Studio) for each slot
        rows = conn.execute(
            """
            SELECT
                e.interval_start,
                e.consumption_kwh as eon_kwh,
                COALESCE(s.consumption_kwh, 0) as studio_kwh,
                (e.consumption_kwh - COALESCE(s.consumption_kwh, 0)) as main_house_kwh
            FROM electricity_readings e
            LEFT JOIN electricity_readings s ON
                e.interval_start = s.interval_start
                AND s.source = 'shelly_studio_phase'
            WHERE e.source = 'eon'
              AND datetime(e.interval_start) >= datetime(?)
              AND datetime(e.interval_start) < datetime(?)
              AND (e.consumption_kwh - COALESCE(s.consumption_kwh, 0)) > ?
            ORDER BY e.interval_start
            """,
            (
                window_start.isoformat(),
                window_end.isoformat(),
                HEATING_KWH_THRESHOLD,
            ),
        ).fetchall()

        if not rows:
            return None

        # Calculate totals
        total_kwh = 0.0
        cheap_kwh = 0.0
        peak_kwh = 0.0
        cheap_slots = 0
        peak_slots = 0

        for row in rows:
            interval_start = datetime.fromisoformat(row["interval_start"])
            main_house_kwh = row["main_house_kwh"]

            # Determine rate period based on hour
            # Handle timezone-aware datetimes by getting the hour
            hour = interval_start.hour

            if hour < CHEAP_HOUR_END:
                cheap_kwh += main_house_kwh
                cheap_slots += 1
            else:
                peak_kwh += main_house_kwh
                peak_slots += 1

            total_kwh += main_house_kwh

        # Calculate cost
        cost_pence = (cheap_kwh * CHEAP_RATE) + (peak_kwh * PEAK_RATE)

        # Get outside temperature at session start
        outside_temp = conn.execute(
            """
            SELECT AVG(temperature_c) as avg_temp
            FROM temperature_readings
            WHERE sensor_id = 'outside_temperature'
              AND DATE(timestamp) = DATE(?)
            """,
            (session_start.isoformat(),),
        ).fetchone()

        outside_temperature_c = outside_temp["avg_temp"] if outside_temp else None

        return SaunaHeatingAnalysis(
            session_id=session_id,
            start_time=session_start,
            peak_temperature_c=peak_temperature_c,
            outside_temperature_c=outside_temperature_c,
            heating_minutes=(cheap_slots + peak_slots) * 30,
            total_kwh=round(total_kwh, 1),
            cheap_kwh=round(cheap_kwh, 1),
            peak_kwh=round(peak_kwh, 1),
            cost_pence=round(cost_pence, 0),
            cheap_slots=cheap_slots,
            peak_slots=peak_slots,
        )


def analyze_all_sessions(db_path: Path | None = None) -> list[SaunaHeatingAnalysis]:
    """Analyze heating for all sauna sessions in the database."""
    analyses = []

    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT id, start_time, peak_temperature_c
               FROM sauna_sessions
               ORDER BY start_time"""
        ).fetchall()

    for row in rows:
        analysis = analyze_session_heating(
            session_start=datetime.fromisoformat(row["start_time"]),
            peak_temperature_c=row["peak_temperature_c"],
            session_id=row["id"],
            db_path=db_path,
        )
        if analysis:
            analyses.append(analysis)

    return analyses


def update_session_electricity_data(db_path: Path | None = None) -> dict:
    """Update electricity-derived fields for all sessions.

    Populates: estimated_kwh, cheap_kwh, peak_kwh, heating_minutes, cost_pence

    Returns counts of updated and skipped sessions.
    """
    updated = 0
    skipped = 0

    analyses = analyze_all_sessions(db_path)

    with get_connection(db_path) as conn:
        for analysis in analyses:
            if analysis.session_id is None:
                skipped += 1
                continue

            conn.execute(
                """UPDATE sauna_sessions
                   SET estimated_kwh = ?,
                       cheap_kwh = ?,
                       peak_kwh = ?,
                       heating_minutes = ?,
                       cost_pence = ?
                   WHERE id = ?""",
                (
                    analysis.total_kwh,
                    analysis.cheap_kwh,
                    analysis.peak_kwh,
                    analysis.heating_minutes,
                    analysis.cost_pence,
                    analysis.session_id,
                ),
            )
            updated += 1

        conn.commit()

    return {"updated": updated, "skipped": skipped}


# Alias for backwards compatibility
update_session_estimated_kwh = update_session_electricity_data
