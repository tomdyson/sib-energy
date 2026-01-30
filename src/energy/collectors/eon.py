"""EON electricity data importer.

Imports data from CSV files exported by the eonapi tool.
CSV format: interval_start, interval_end, consumption_kwh
"""

import csv
from datetime import datetime
from pathlib import Path

from ..db import get_connection
from ..models import ElectricityReading
from ..tariffs import calculate_cost

SOURCE_NAME = "eon"


def parse_csv(csv_path: Path) -> list[ElectricityReading]:
    """Parse an eonapi CSV export file."""
    readings = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            readings.append(
                ElectricityReading(
                    source=SOURCE_NAME,
                    interval_start=datetime.fromisoformat(row["interval_start"]),
                    interval_end=datetime.fromisoformat(row["interval_end"]),
                    consumption_kwh=float(row["consumption_kwh"]),
                )
            )
    return readings


def import_from_csv(csv_path: Path, db_path: Path | None = None, calculate_costs: bool = True) -> dict:
    """Import electricity readings from an eonapi CSV file.

    Returns dict with 'imported' and 'skipped' counts.
    """
    readings = parse_csv(csv_path)
    return save_readings(readings, db_path, calculate_costs)


def save_readings(
    readings: list[ElectricityReading], db_path: Path | None = None, calculate_costs: bool = True
) -> dict:
    """Save electricity readings to the database.

    Returns dict with 'imported' and 'skipped' counts.
    """
    imported = 0
    skipped = 0

    with get_connection(db_path) as conn:
        for reading in readings:
            # Calculate cost if requested and tariff exists
            cost = None
            if calculate_costs:
                try:
                    cost = calculate_cost(reading.consumption_kwh, reading.interval_start, db_path)
                except ValueError:
                    pass  # No tariff for this time

            try:
                conn.execute(
                    """INSERT INTO electricity_readings
                       (source, interval_start, interval_end, consumption_kwh, cost_pence)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        reading.source,
                        reading.interval_start.isoformat(),
                        reading.interval_end.isoformat(),
                        reading.consumption_kwh,
                        cost,
                    ),
                )
                imported += 1
            except Exception:
                # Likely duplicate (UNIQUE constraint)
                skipped += 1

        conn.commit()

    return {"imported": imported, "skipped": skipped}


def get_latest_reading(db_path: Path | None = None) -> datetime | None:
    """Get the timestamp of the most recent EON reading."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(interval_start) as latest FROM electricity_readings WHERE source = ?",
            (SOURCE_NAME,),
        ).fetchone()
        if row and row["latest"]:
            return datetime.fromisoformat(row["latest"])
        return None
