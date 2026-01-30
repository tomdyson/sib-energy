"""Shelly Pro 3EM local CSV data importer.

Fetches historical data from the device's local HTTP API and imports it
into the database. Supports date filtering to avoid fetching the entire history.

Endpoint: http://<ip>/em1data/<channel>/data.csv
Parameters:
  - add_keys=true: Include column headers
  - ts: Start timestamp (UNIX seconds)
  - end_ts: End timestamp (UNIX seconds)
"""

import csv
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from ..db import get_connection
from ..models import ElectricityReading
from ..tariffs import calculate_cost

SOURCE_NAME = "shelly_studio_phase"

# CSV columns from the Shelly Pro 3EM (EM1Data component)
# timestamp,total_act_energy,total_act_ret_energy,lag_react_energy,lead_react_energy,
# max_act_power,min_act_power,max_aprt_power,min_aprt_power,
# max_voltage,min_voltage,avg_voltage,max_current,min_current,avg_current


def fetch_csv_data(
    ip: str,
    channel: int = 2,
    start_ts: int | None = None,
    end_ts: int | None = None,
    timeout: float = 300.0,
) -> str:
    """Fetch CSV data from the Shelly device.

    Args:
        ip: Device IP address
        channel: EM1Data channel number (0, 1, or 2 for Pro 3EM)
        start_ts: Start timestamp (UNIX seconds), None for all data
        end_ts: End timestamp (UNIX seconds), None for no limit
        timeout: Request timeout in seconds (default 5 minutes for large fetches)

    Returns:
        Raw CSV string with headers
    """
    url = f"http://{ip}/em1data/{channel}/data.csv"
    params = {"add_keys": "true"}

    if start_ts is not None:
        params["ts"] = str(start_ts)
    if end_ts is not None:
        params["end_ts"] = str(end_ts)

    with httpx.Client(timeout=timeout) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.text


def parse_csv_data(csv_data: str) -> list[dict]:
    """Parse CSV data from Shelly into list of dicts.

    Each row represents one minute of data with:
    - timestamp: UNIX timestamp
    - total_act_energy: Wh consumed in this minute
    - Various power/voltage/current metrics
    """
    rows = []
    reader = csv.DictReader(io.StringIO(csv_data))
    for row in reader:
        rows.append(row)
    return rows


def aggregate_to_30min(rows: list[dict]) -> list[ElectricityReading]:
    """Aggregate per-minute readings to 30-minute intervals.

    Groups readings by 30-minute window and sums the energy consumption.
    """
    if not rows:
        return []

    # Group by 30-minute interval
    intervals: dict[datetime, float] = {}

    for row in rows:
        ts = int(row["timestamp"])
        energy_wh = float(row["total_act_energy"])

        # Convert to datetime and round down to 30-min boundary
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        if dt.minute >= 30:
            interval_start = dt.replace(minute=30, second=0, microsecond=0)
        else:
            interval_start = dt.replace(minute=0, second=0, microsecond=0)

        # Accumulate energy
        if interval_start not in intervals:
            intervals[interval_start] = 0.0
        intervals[interval_start] += energy_wh

    # Convert to ElectricityReading objects
    readings = []
    for interval_start, energy_wh in sorted(intervals.items()):
        interval_end = interval_start + timedelta(minutes=30)
        consumption_kwh = energy_wh / 1000.0

        readings.append(
            ElectricityReading(
                source=SOURCE_NAME,
                interval_start=interval_start,
                interval_end=interval_end,
                consumption_kwh=consumption_kwh,
            )
        )

    return readings


def save_readings(
    readings: list[ElectricityReading], db_path: Path | None = None, calculate_costs: bool = True
) -> dict:
    """Save electricity readings to the database.

    Returns dict with 'imported' and 'skipped' counts.
    Duplicates are automatically skipped due to UNIQUE constraint.
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


def fetch_and_import(
    ip: str,
    channel: int = 2,
    days: int = 30,
    db_path: Path | None = None,
) -> dict:
    """Fetch data from Shelly device and import to database.

    Args:
        ip: Device IP address
        channel: EM1Data channel number
        days: Number of days to fetch (default: 30)
        db_path: Path to database

    Returns:
        dict with 'imported', 'skipped', and 'raw_rows' counts
    """
    # Calculate timestamps
    now = datetime.now(tz=timezone.utc)
    start_ts = int((now - timedelta(days=days)).timestamp())
    end_ts = int(now.timestamp())

    # Fetch CSV data
    csv_data = fetch_csv_data(ip, channel, start_ts, end_ts)

    # Parse and aggregate
    rows = parse_csv_data(csv_data)
    readings = aggregate_to_30min(rows)

    # Save to database
    result = save_readings(readings, db_path)
    result["raw_rows"] = len(rows)
    result["aggregated_intervals"] = len(readings)

    return result


def get_latest_reading(db_path: Path | None = None) -> datetime | None:
    """Get the timestamp of the most recent Shelly CSV reading."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(interval_start) as latest FROM electricity_readings WHERE source = ?",
            (SOURCE_NAME,),
        ).fetchone()
        if row and row["latest"]:
            return datetime.fromisoformat(row["latest"])
        return None
