"""Home Assistant data collector.

Fetches historical data from Home Assistant's REST API.
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..db import get_connection
from ..models import TemperatureReading

# Default configuration
DEFAULT_BASE_URL = "http://192.168.5.120:8123"
DEFAULT_ENTITY_ID = "sensor.shelly_studio_therm_temperature"


class HomeAssistantError(Exception):
    """Base exception for Home Assistant collector errors."""
    pass


def get_token() -> str:
    """Get the HA token from environment variables."""
    token = os.environ.get("HA_TOKEN")
    if not token:
        raise HomeAssistantError("HA_TOKEN environment variable not set")
    return token


def fetch_history(
    days: int = 30,
    entity_id: str = DEFAULT_ENTITY_ID,
    base_url: str = DEFAULT_BASE_URL,
    token: str | None = None,
) -> list[TemperatureReading]:
    """Fetch history from Home Assistant API.

    Args:
        days: Number of days of history to fetch
        entity_id: The entity ID to fetch history for
        base_url: The base URL of the Home Assistant instance
        token: Access token (defaults to HA_TOKEN env var)

    Returns:
        List of TemperatureReading objects
    """
    if token is None:
        token = get_token()

    # Calculate start time
    start_time = datetime.now() - timedelta(days=days)
    timestamp = start_time.isoformat()

    # Removing trailing slash if present
    base_url = base_url.rstrip("/")
    
    url = f"{base_url}/api/history/period/{timestamp}?filter_entity_id={entity_id}&end_time={datetime.now().isoformat()}&minimal_response=false&no_attributes=true"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=60.0) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise HomeAssistantError(f"Network error connecting to Home Assistant: {e}")
    except urllib.error.HTTPError as e:
        raise HomeAssistantError(f"HTTP error from Home Assistant: {e.code} - {e.reason}")

    if not data or not isinstance(data, list) or len(data) == 0:
        return []

    # HA returns a list of lists (one list per entity)
    # Since we filtered by one entity ID, we expect data[0] to be our list of states
    entity_data = data[0]
    
    readings = []
    sensor_id = "studio_temperature"  # Normalized ID for our DB

    for state in entity_data:
        try:
            state_val = state.get("state")
            if state_val in ("unavailable", "unknown", None):
                continue
                
            # Parse timestamp (HA returns ISO 8601)
            # Example: 2026-01-29T10:00:00.123456+00:00
            ts_str = state.get("last_updated") or state.get("last_changed")
            if not ts_str:
                continue
                
            timestamp = datetime.fromisoformat(ts_str)
            temperature = float(state_val)

            readings.append(
                TemperatureReading(
                    sensor_id=sensor_id,
                    timestamp=timestamp,
                    temperature_c=temperature,
                )
            )
        except (ValueError, TypeError):
            # Skip invalid readings
            continue

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


def import_ha_data(
    days: int = 30,
    entity_id: str = DEFAULT_ENTITY_ID,
    base_url: str = DEFAULT_BASE_URL,
    db_path: Path | None = None,
) -> dict:
    """Fetch data from HA and import to database.

    Returns dict with 'imported' and 'skipped' counts.
    """
    readings = fetch_history(days=days, entity_id=entity_id, base_url=base_url)
    return save_readings(readings, db_path)
