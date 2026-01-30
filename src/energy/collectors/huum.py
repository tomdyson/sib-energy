"""Huum sauna temperature data collector.

Imports data from:
1. Text files exported by huum-cli (table format)
2. Huum API directly (future)
"""

import re
from datetime import datetime
from pathlib import Path

from ..db import get_connection
from ..models import TemperatureReading

SENSOR_ID = "sauna"


def parse_huum_table(file_path: Path) -> list[TemperatureReading]:
    """Parse a huum-cli table output file.

    Expected format (with box-drawing characters):
    │ 2026-01-01 05:32:15 │              0°C │
    │ 2026-01-01 05:36:27 │              2°C │
    """
    readings = []
    # Pattern matches: timestamp and temperature from table rows
    pattern = re.compile(r"│\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s*│\s*(-?\d+)°C\s*│")

    with open(file_path, encoding="utf-8") as f:
        for line in f:
            match = pattern.search(line)
            if match:
                timestamp_str, temp_str = match.groups()
                readings.append(
                    TemperatureReading(
                        sensor_id=SENSOR_ID,
                        timestamp=datetime.fromisoformat(timestamp_str.replace(" ", "T")),
                        temperature_c=float(temp_str),
                    )
                )

    return readings


def import_from_file(file_path: Path, db_path: Path | None = None) -> dict:
    """Import temperature readings from a huum-cli export file.

    Returns dict with 'imported' and 'skipped' counts.
    """
    readings = parse_huum_table(file_path)
    return save_readings(readings, db_path)


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


def get_latest_reading(db_path: Path | None = None) -> datetime | None:
    """Get the timestamp of the most recent sauna reading."""
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
    """Get all sauna readings within a time period."""
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
