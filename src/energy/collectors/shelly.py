"""Shelly Cloud API data collector.

Fetches power consumption data from Shelly devices via the Shelly Cloud API.
Supports Shelly 3EM (single phase) with 30-minute interval aggregation.
"""

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from ..db import get_connection
from ..models import ElectricityReading
from ..tariffs import calculate_cost

SOURCE_NAME = "shelly_phase1"
SHELLY_API_BASE = "https://shelly-{server_id}-eu.shelly.cloud"


def get_auth_key() -> str:
    """Get Shelly Cloud auth key from environment."""
    key = os.environ.get("SHELLY_AUTH_KEY")
    if not key:
        raise ValueError(
            "SHELLY_AUTH_KEY environment variable not set.\n"
            "Get your key from https://control.shelly.cloud/ (User Settings > Authorization Cloud Key)\n"
            "Then set it: export SHELLY_AUTH_KEY='your-key-here'"
        )
    return key


def get_device_id() -> str:
    """Get Shelly device ID from environment."""
    device_id = os.environ.get("SHELLY_DEVICE_ID")
    if not device_id:
        raise ValueError(
            "SHELLY_DEVICE_ID environment variable not set.\n"
            "Find your device ID in Shelly Cloud or use the 'list-devices' command.\n"
            "Then set it: export SHELLY_DEVICE_ID='your-device-id'"
        )
    return device_id


def get_server_id() -> str:
    """Get Shelly Cloud server ID from environment (default: 103)."""
    return os.environ.get("SHELLY_SERVER_ID", "103")


def list_devices(auth_key: str | None = None, server_id: str | None = None) -> list[dict[str, Any]]:
    """List all Shelly devices on your account.

    Returns list of devices with their IDs, names, and detailed info.
    """
    if auth_key is None:
        auth_key = get_auth_key()
    if server_id is None:
        server_id = get_server_id()

    url = SHELLY_API_BASE.format(server_id=server_id) + "/device/all_status"

    with httpx.Client() as client:
        response = client.post(
            url,
            json={"auth_key": auth_key},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("isok"):
            raise ValueError(f"Shelly API error: {data}")

        devices = []
        for device_id, device_data in data.get("data", {}).get("devices_status", {}).items():
            # Try different locations for device info
            sys_info = device_data.get("sys", {})
            dev_info = device_data.get("_dev_info", {})

            # Get device type/model from code field or _dev_info
            device_type = (
                device_data.get("code")
                or dev_info.get("device_type")
                or dev_info.get("gen")
                or "Unknown"
            )

            # Get MAC from sys.mac or _dev_info
            mac = sys_info.get("mac") or dev_info.get("mac") or "Unknown"

            # Detect if device has metering capability
            has_meter = any(
                key in device_data
                for key in ["emeters", "meters", "emeter", "switch:0"]  # switch often has power data
            )

            devices.append({
                "id": device_id,
                "name": device_data.get("name") or "Unnamed",
                "type": device_type,
                "mac": mac,
                "fw_version": sys_info.get("available_updates", {}).get("stable", {}).get("version")
                or dev_info.get("fw")
                or "Unknown",
                "online": device_data.get("cloud", {}).get("connected", False),
                "has_meter": has_meter,
                "ip": device_data.get("eth", {}).get("ip")
                or device_data.get("wifi", {}).get("sta_ip")
                or "Unknown",
            })

        return devices


def fetch_statistics(
    device_id: str,
    start_time: datetime,
    end_time: datetime,
    auth_key: str | None = None,
    server_id: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch power consumption statistics from Shelly Cloud.

    Returns raw statistics data from the API.
    """
    if auth_key is None:
        auth_key = get_auth_key()
    if server_id is None:
        server_id = get_server_id()

    url = SHELLY_API_BASE.format(server_id=server_id) + "/statistics"

    with httpx.Client() as client:
        response = client.post(
            url,
            json={
                "auth_key": auth_key,
                "device_id": device_id,
                "channel": 0,  # First phase
                "date_from": start_time.isoformat(),
                "date_to": end_time.isoformat(),
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        if not data.get("isok"):
            raise ValueError(f"Shelly API error: {data}")

        return data.get("data", {}).get("statistics", [])


def aggregate_to_30min(raw_data: list[dict[str, Any]]) -> list[ElectricityReading]:
    """Aggregate Shelly minute-level data into 30-minute intervals.

    Shelly returns data points with timestamps and power values (watts).
    We need to:
    1. Group by 30-minute buckets
    2. Calculate energy consumption (kWh) for each bucket
    3. Align to half-hour boundaries (00:00, 00:30, 01:00, etc.)
    """
    if not raw_data:
        return []

    # Parse and sort data by timestamp
    data_points = []
    for point in raw_data:
        timestamp = datetime.fromisoformat(point["datetime"].replace("Z", "+00:00"))
        watts = float(point.get("consumption", 0))  # Average power in watts for this minute
        data_points.append((timestamp, watts))

    data_points.sort(key=lambda x: x[0])

    # Group into 30-minute buckets
    readings = []
    current_bucket_start = None
    bucket_watt_minutes = []

    for timestamp, watts in data_points:
        # Calculate bucket start (round down to nearest 30 minutes)
        minute = 0 if timestamp.minute < 30 else 30
        bucket_start = timestamp.replace(minute=minute, second=0, microsecond=0)
        bucket_end = bucket_start + timedelta(minutes=30)

        if current_bucket_start is None:
            current_bucket_start = bucket_start

        if bucket_start != current_bucket_start:
            # Save previous bucket
            if bucket_watt_minutes:
                # Convert watt-minutes to kWh: sum(watts * 1 minute) / 60 / 1000
                consumption_kwh = sum(bucket_watt_minutes) / 60 / 1000
                readings.append(
                    ElectricityReading(
                        source=SOURCE_NAME,
                        interval_start=current_bucket_start,
                        interval_end=current_bucket_start + timedelta(minutes=30),
                        consumption_kwh=consumption_kwh,
                    )
                )

            # Start new bucket
            current_bucket_start = bucket_start
            bucket_watt_minutes = [watts]
        else:
            bucket_watt_minutes.append(watts)

    # Don't forget the last bucket
    if bucket_watt_minutes and current_bucket_start:
        consumption_kwh = sum(bucket_watt_minutes) / 60 / 1000
        readings.append(
            ElectricityReading(
                source=SOURCE_NAME,
                interval_start=current_bucket_start,
                interval_end=current_bucket_start + timedelta(minutes=30),
                consumption_kwh=consumption_kwh,
            )
        )

    return readings


def fetch_and_import(
    start_time: datetime,
    end_time: datetime,
    db_path: Path | None = None,
    device_id: str | None = None,
    auth_key: str | None = None,
    server_id: str | None = None,
    calculate_costs: bool = True,
) -> dict:
    """Fetch data from Shelly Cloud and import into database.

    Returns dict with 'imported' and 'skipped' counts.
    """
    if device_id is None:
        device_id = get_device_id()

    # Fetch raw data
    raw_data = fetch_statistics(device_id, start_time, end_time, auth_key, server_id)

    # Aggregate to 30-minute intervals
    readings = aggregate_to_30min(raw_data)

    # Save to database
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
