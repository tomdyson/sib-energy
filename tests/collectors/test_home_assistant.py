"""Tests for Home Assistant collector."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import urllib.error

import pytest
from energy.collectors import home_assistant
from energy.models import TemperatureReading


@pytest.fixture
def mock_urlopen():
    with patch("urllib.request.urlopen") as mock:
        yield mock


def test_fetch_history_success(mock_urlopen):
    """Test successful fetching and parsing of history."""
    # Mock response data
    mock_data = [
        [
            {
                "entity_id": "sensor.shelly_studio_therm_temperature",
                "state": "20.5",
                "last_changed": "2026-01-29T10:00:00+00:00",
                "last_updated": "2026-01-29T10:00:00+00:00",
            },
            {
                "entity_id": "sensor.shelly_studio_therm_temperature",
                "state": "21.0",
                "last_changed": "2026-01-29T10:30:00+00:00",
                "last_updated": "2026-01-29T10:30:00+00:00",
            },
            {
                "entity_id": "sensor.shelly_studio_therm_temperature",
                "state": "unavailable",
                "last_changed": "2026-01-29T11:00:00+00:00",
            },
        ]
    ]

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(mock_data).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response

    readings = home_assistant.fetch_history(
        days=1,
        entity_id="sensor.test",
        base_url="http://ha.local",
        token="test-token",
    )

    assert len(readings) == 2
    assert readings[0].temperature_c == 20.5
    assert readings[0].timestamp == datetime(2026, 1, 29, 10, 0, 0, tzinfo=timezone.utc)
    assert readings[0].sensor_id == "studio_temperature"
    
    assert readings[1].temperature_c == 21.0


def test_fetch_history_network_error(mock_urlopen):
    """Test handling of network errors."""
    mock_urlopen.side_effect = urllib.error.URLError("Network error")

    with pytest.raises(home_assistant.HomeAssistantError, match="Network error"):
        home_assistant.fetch_history(token="test-token")


def test_fetch_history_http_error(mock_urlopen):
    """Test handling of HTTP errors."""
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="http://test", code=404, msg="Not Found", hdrs={}, fp=None
    )

    with pytest.raises(home_assistant.HomeAssistantError, match="404"):
        home_assistant.fetch_history(token="test-token")
