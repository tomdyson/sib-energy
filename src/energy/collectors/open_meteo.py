"""Open-Meteo weather data collector.

Fetches historical outside temperature data from the Open-Meteo Archive API
for correlation with energy consumption patterns.
"""

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from ..db import get_connection
from ..models import TemperatureReading

SENSOR_ID = "outside_temperature"
API_BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Default location (configurable via CLI)
DEFAULT_LATITUDE = 51.989
DEFAULT_LONGITUDE = -1.497
DEFAULT_TIMEZONE = "Europe/London"


def fetch_from_api(
    days: int = 30,
    latitude: float = DEFAULT_LATITUDE,
    longitude: float = DEFAULT_LONGITUDE,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[TemperatureReading]:
    """Fetch hourly temperature data from Open-Meteo Archive API.

    Args:
        days: Number of days to fetch (used if start_date not provided)
        latitude: Location latitude
        longitude: Location longitude
        start_date: Start date (YYYY-MM-DD format), defaults to (today - days)
        end_date: End date (YYYY-MM-DD format), defaults to yesterday

    Returns:
        List of TemperatureReading objects with sensor_id='outside_temperature'

    Note:
        The Archive API has a 5-7 day delay, so 'yesterday' is typically
        the most recent available data.
    """
    tz = ZoneInfo(DEFAULT_TIMEZONE)
    today = datetime.now(tz).date()

    # Default end_date to yesterday (archive has delay)
    if end_date is None:
        end = today - timedelta(days=1)
        end_date = end.isoformat()

    # Default start_date based on days parameter
    if start_date is None:
        start = today - timedelta(days=days)
        start_date = start.isoformat()

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": "temperature_2m",
        "timezone": DEFAULT_TIMEZONE,
    }

    response = httpx.get(API_BASE_URL, params=params, timeout=30.0)
    response.raise_for_status()
    data = response.json()

    readings = []
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])

    for time_str, temp in zip(times, temps):
        if temp is not None:  # Skip missing values
            # Parse timestamp and make timezone-aware
            timestamp = datetime.fromisoformat(time_str)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=tz)

            readings.append(
                TemperatureReading(
                    sensor_id=SENSOR_ID,
                    timestamp=timestamp,
                    temperature_c=float(temp),
                )
            )

    return readings


def save_readings(readings: list[TemperatureReading], db_path: Path | None = None) -> dict:
    """Save temperature readings to the database.

    Returns dict with 'imported' and 'skipped' counts.
    """
    imported = 0
    skipped = 0

    with get_connection(db_path) as conn:
        for reading in readings:
            try:
                conn.execute(
                    """INSERT INTO temperature_readings
                       (sensor_id, timestamp, temperature_c)
                       VALUES (?, ?, ?)""",
                    (
                        reading.sensor_id,
                        reading.timestamp.isoformat(),
                        reading.temperature_c,
                    ),
                )
                imported += 1
            except Exception:
                # Likely duplicate (UNIQUE constraint)
                skipped += 1

        conn.commit()

    return {"imported": imported, "skipped": skipped}


def import_weather_data(
    days: int = 30,
    latitude: float = DEFAULT_LATITUDE,
    longitude: float = DEFAULT_LONGITUDE,
    db_path: Path | None = None,
) -> dict:
    """Fetch weather data from Open-Meteo and import to database.

    Returns dict with 'imported' and 'skipped' counts.
    """
    readings = fetch_from_api(days=days, latitude=latitude, longitude=longitude)
    return save_readings(readings, db_path)


def get_latest_reading(db_path: Path | None = None) -> datetime | None:
    """Get the timestamp of the most recent outside temperature reading."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(timestamp) as latest FROM temperature_readings WHERE sensor_id = ?",
            (SENSOR_ID,),
        ).fetchone()
        if row and row["latest"]:
            return datetime.fromisoformat(row["latest"])
        return None


def get_readings_for_period(
    start: datetime, end: datetime, db_path: Path | None = None
) -> list[TemperatureReading]:
    """Get all outside temperature readings within a time period."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """SELECT sensor_id, timestamp, temperature_c
               FROM temperature_readings
               WHERE sensor_id = ? AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp""",
            (SENSOR_ID, start.isoformat(), end.isoformat()),
        ).fetchall()

        return [
            TemperatureReading(
                sensor_id=row["sensor_id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                temperature_c=row["temperature_c"],
            )
            for row in rows
        ]
