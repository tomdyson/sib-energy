"""Shelly local HTTP API data collector.

Fetches power consumption data directly from Shelly devices on your local network.
Supports Shelly 3EM with 30-minute interval aggregation.

This uses the local HTTP API (Gen 1 and Gen 2) which doesn't require cloud access.
"""

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from ..db import get_connection
from ..models import ElectricityReading
from ..tariffs import calculate_cost

# Load environment variables from .env file
load_dotenv()

SOURCE_NAME = "shelly_phase1"


def get_device_ip() -> str:
    """Get Shelly device IP from environment."""
    ip = os.environ.get("SHELLY_LOCAL_IP")
    if not ip:
        raise ValueError(
            "SHELLY_LOCAL_IP environment variable not set.\n"
            "Set it to your Shelly 3EM's local IP address (e.g., 10.230.1.x)\n"
            "Example: export SHELLY_LOCAL_IP='10.230.1.100'"
        )
    return ip


def get_channel() -> int:
    """Get Shelly 3EM channel to monitor (default: 0 for first phase)."""
    return int(os.environ.get("SHELLY_CHANNEL", "0"))


def detect_generation(ip: str, timeout: float = 15.0) -> str:
    """Detect if device is Gen 1 or Gen 2.

    Gen 1: Uses /status endpoint
    Gen 2: Uses /rpc/Shelly.GetStatus endpoint
    """
    with httpx.Client(timeout=timeout) as client:
        # Try Gen 2 first
        try:
            response = client.get(f"http://{ip}/rpc/Shelly.GetDeviceInfo")
            if response.status_code == 200:
                return "gen2"
        except Exception:
            pass

        # Try Gen 1
        try:
            response = client.get(f"http://{ip}/status")
            if response.status_code == 200:
                data = response.json()
                if "emeters" in data:  # 3EM has emeters
                    return "gen1"
        except Exception:
            pass

    raise ValueError(
        f"Could not detect Shelly generation at {ip}. "
        "Check that the IP is correct and the device is online."
    )


def get_device_info(ip: str, timeout: float = 15.0) -> dict[str, Any]:
    """Get device information (model, MAC, etc.)."""
    generation = detect_generation(ip, timeout)

    with httpx.Client(timeout=timeout) as client:
        if generation == "gen2":
            response = client.get(f"http://{ip}/rpc/Shelly.GetDeviceInfo")
            response.raise_for_status()
            info = response.json()
            return {
                "generation": "gen2",
                "model": info.get("model", "Unknown"),
                "mac": info.get("mac", "Unknown"),
                "fw_version": info.get("fw_id", "Unknown"),
            }
        else:  # gen1
            response = client.get(f"http://{ip}/shelly")
            response.raise_for_status()
            info = response.json()
            return {
                "generation": "gen1",
                "model": info.get("type", "Unknown"),
                "mac": info.get("mac", "Unknown"),
                "fw_version": info.get("fw", "Unknown"),
            }


def fetch_current_status(ip: str, channel: int = 0, timeout: float = 15.0) -> dict[str, Any]:
    """Fetch current power status from Shelly device.

    Returns current power (watts) and total energy (Wh) for the specified channel.
    """
    generation = detect_generation(ip, timeout)

    with httpx.Client(timeout=timeout) as client:
        if generation == "gen2":
            response = client.get(f"http://{ip}/rpc/Shelly.GetStatus")
            response.raise_for_status()
            data = response.json()

            # Gen 2 3EM structure: power in em1:{channel}, energy in em1data:{channel}
            if f"em1:{channel}" in data and f"em1data:{channel}" in data:
                em_power = data[f"em1:{channel}"]
                em_data = data[f"em1data:{channel}"]
                return {
                    "power": em_power.get("act_power", 0),  # Current power in watts
                    "total": em_data.get("total_act_energy", 0),  # Total energy in watt-hours
                    "timestamp": datetime.now(),
                }
        else:  # gen1
            response = client.get(f"http://{ip}/status")
            response.raise_for_status()
            data = response.json()

            # Gen 1 3EM structure
            if "emeters" in data and len(data["emeters"]) > channel:
                em_data = data["emeters"][channel]
                return {
                    "power": em_data.get("power", 0),  # Current power in watts
                    "total": em_data.get("total", 0),  # Total energy in watt-hours
                    "timestamp": datetime.now(),
                }

    raise ValueError(f"Could not read power data from channel {channel}")


def collect_readings_from_polling(
    ip: str,
    channel: int = 0,
    interval_minutes: int = 30,
    db_path: Path | None = None,
) -> dict:
    """Collect a single 30-minute reading by polling current status.

    This is meant to be run periodically (e.g., every 30 minutes via cron).
    It calculates energy consumption since the last reading based on the device's
    total energy counter.

    Returns dict with 'imported' and 'skipped' counts.
    """
    # Get current status
    current = fetch_current_status(ip, channel)
    current_total_wh = current["total"]
    current_time = current["timestamp"]

    # Get last reading from database
    last_reading = get_latest_reading(db_path)

    if last_reading is None:
        # First reading - store the baseline but don't create a consumption record yet
        with get_connection(db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO shelly_baseline
                   (source, last_total_wh, last_timestamp)
                   VALUES (?, ?, ?)""",
                (SOURCE_NAME, current_total_wh, current_time.isoformat()),
            )
            conn.commit()
        return {"imported": 0, "skipped": 0, "message": "Baseline established"}

    # Get baseline from last poll
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT last_total_wh, last_timestamp FROM shelly_baseline WHERE source = ?",
            (SOURCE_NAME,),
        ).fetchone()

        if row:
            last_total_wh = row["last_total_wh"]
            last_time = datetime.fromisoformat(row["last_timestamp"])

            # Calculate consumption since last reading
            consumption_wh = current_total_wh - last_total_wh
            consumption_kwh = consumption_wh / 1000

            # Create interval boundaries (align to 30-min intervals)
            interval_end = current_time.replace(second=0, microsecond=0)
            if interval_end.minute >= 30:
                interval_end = interval_end.replace(minute=30)
            else:
                interval_end = interval_end.replace(minute=0)

            interval_start = interval_end - timedelta(minutes=interval_minutes)

            # Create reading
            reading = ElectricityReading(
                source=SOURCE_NAME,
                interval_start=interval_start,
                interval_end=interval_end,
                consumption_kwh=consumption_kwh,
            )

            # Save reading
            result = save_readings([reading], db_path, calculate_costs=True)

            # Update baseline
            conn.execute(
                """INSERT OR REPLACE INTO shelly_baseline
                   (source, last_total_wh, last_timestamp)
                   VALUES (?, ?, ?)""",
                (SOURCE_NAME, current_total_wh, current_time.isoformat()),
            )
            conn.commit()

            return result

    return {"imported": 0, "skipped": 0}


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
            # Calculate cost if requested
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
    """Get the timestamp of the most recent Shelly reading."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(interval_start) as latest FROM electricity_readings WHERE source = ?",
            (SOURCE_NAME,),
        ).fetchone()
        if row and row["latest"]:
            return datetime.fromisoformat(row["latest"])
        return None
