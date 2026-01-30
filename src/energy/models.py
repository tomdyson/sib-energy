"""Data models for energy readings and tariffs."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ElectricityReading:
    """A single electricity reading."""

    source: str
    interval_start: datetime
    interval_end: datetime
    consumption_kwh: float
    cost_pence: float | None = None


@dataclass
class TemperatureReading:
    """A single temperature reading."""

    sensor_id: str
    timestamp: datetime
    temperature_c: float


@dataclass
class TariffRate:
    """A rate period within a tariff."""

    start_time: str  # HH:MM format
    end_time: str  # HH:MM format
    rate_pence_per_kwh: float
    days: str = "*"  # '*' = all, 'weekdays', 'weekends'


@dataclass
class Tariff:
    """An electricity tariff with time-of-use rates."""

    name: str
    valid_from: datetime
    valid_to: datetime | None
    rates: list[TariffRate]


@dataclass
class SaunaSession:
    """A detected sauna usage session."""

    start_time: datetime
    end_time: datetime
    duration_minutes: int
    peak_temperature_c: float
    estimated_kwh: float | None = None
