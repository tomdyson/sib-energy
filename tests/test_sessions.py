import pytest
from datetime import datetime, timedelta
from energy.models import TemperatureReading
from energy.analysis.sessions import detect_sessions

def test_detect_sessions_basic():
    """Test a basic sauna session with outdoor temp correlation."""
    # Outdoor is 10C
    outside = [
        TemperatureReading("outside", datetime(2026, 1, 1, 10), 10.0),
        TemperatureReading("outside", datetime(2026, 1, 1, 11), 10.0),
    ]
    
    # Sauna heats up from 10C to 70C, then cools down
    sauna = [
        TemperatureReading("sauna", datetime(2026, 1, 1, 10, 0), 10.0),
        TemperatureReading("sauna", datetime(2026, 1, 1, 10, 5), 16.0), # Start: > 10+5, trend up
        TemperatureReading("sauna", datetime(2026, 1, 1, 10, 10), 25.0),
        TemperatureReading("sauna", datetime(2026, 1, 1, 11, 0), 70.0),  # Peak
        TemperatureReading("sauna", datetime(2026, 1, 1, 11, 10), 59.0), # End: < 60
        TemperatureReading("sauna", datetime(2026, 1, 1, 11, 20), 50.0),
    ]
    
    sessions = detect_sessions(sauna, outside)
    assert len(sessions) == 1
    assert sessions[0].peak_temperature_c == 70.0
    assert sessions[0].start_time == datetime(2026, 1, 1, 10, 5)
    assert sessions[0].end_time == datetime(2026, 1, 1, 11, 10)

def test_detect_sessions_no_peak():
    """Test a session that doesn't reach the minimum peak temperature."""
    outside = [TemperatureReading("outside", datetime(2026, 1, 1, 10), 10.0)]
    sauna = [
        TemperatureReading("sauna", datetime(2026, 1, 1, 10, 0), 10.0),
        TemperatureReading("sauna", datetime(2026, 1, 1, 10, 5), 20.0),
        TemperatureReading("sauna", datetime(2026, 1, 1, 10, 10), 50.0), # Doesn't reach 65
        TemperatureReading("sauna", datetime(2026, 1, 1, 11, 0), 40.0),
    ]
    
    sessions = detect_sessions(sauna, outside)
    assert len(sessions) == 0

def test_detect_sessions_cold_start():
    """Test a session starting from 0C in winter."""
    outside = [TemperatureReading("outside", datetime(2026, 1, 1, 10), 0.0)]
    sauna = [
        TemperatureReading("sauna", datetime(2026, 1, 1, 10, 0), 0.0),
        TemperatureReading("sauna", datetime(2026, 1, 1, 10, 5), 6.0),  # > 0+5, trend up
        TemperatureReading("sauna", datetime(2026, 1, 1, 11, 0), 68.0),
        TemperatureReading("sauna", datetime(2026, 1, 1, 12, 0), 58.0),
    ]
    
    sessions = detect_sessions(sauna, outside)
    assert len(sessions) == 1
    assert sessions[0].start_time == datetime(2026, 1, 1, 10, 5)

def test_detect_sessions_trend_check():
    """Test that trend check (next > current) prevents false starts."""
    outside = [TemperatureReading("outside", datetime(2026, 1, 1, 10), 20.0)]
    sauna = [
        TemperatureReading("sauna", datetime(2026, 1, 1, 10, 0), 26.0), # > 20+5
        TemperatureReading("sauna", datetime(2026, 1, 1, 10, 5), 25.0), # Trend DOWN
        TemperatureReading("sauna", datetime(2026, 1, 1, 10, 10), 28.0), # > 20+5
        TemperatureReading("sauna", datetime(2026, 1, 1, 10, 15), 35.0), # Trend UP
        TemperatureReading("sauna", datetime(2026, 1, 1, 11, 0), 66.0),
        TemperatureReading("sauna", datetime(2026, 1, 1, 11, 30), 55.0),
    ]
    
    sessions = detect_sessions(sauna, outside)
    assert len(sessions) == 1
    assert sessions[0].start_time == datetime(2026, 1, 1, 10, 10)
