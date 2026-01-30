"""Database connection and schema management."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "home-energy" / "energy.db"

SCHEMA = """
-- Electricity readings (half-hourly from EON, plus Shelly data)
CREATE TABLE IF NOT EXISTS electricity_readings (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    interval_start TEXT NOT NULL,
    interval_end TEXT NOT NULL,
    consumption_kwh REAL NOT NULL,
    cost_pence REAL,
    UNIQUE(source, interval_start)
);

-- Temperature readings (sauna and future sensors)
CREATE TABLE IF NOT EXISTS temperature_readings (
    id INTEGER PRIMARY KEY,
    sensor_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    temperature_c REAL NOT NULL,
    UNIQUE(sensor_id, timestamp)
);

-- Tariff definitions
CREATE TABLE IF NOT EXISTS tariffs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT
);

-- Tariff rate periods
CREATE TABLE IF NOT EXISTS tariff_rates (
    id INTEGER PRIMARY KEY,
    tariff_id INTEGER NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    rate_pence_per_kwh REAL NOT NULL,
    days TEXT DEFAULT '*',
    FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
);

-- Detected sauna sessions (derived from temperature data)
CREATE TABLE IF NOT EXISTS sauna_sessions (
    id INTEGER PRIMARY KEY,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    duration_minutes INTEGER,
    peak_temperature_c REAL,
    estimated_kwh REAL
);

-- Shelly polling baseline (for local API collectors)
CREATE TABLE IF NOT EXISTS shelly_baseline (
    source TEXT PRIMARY KEY,
    last_total_wh REAL NOT NULL,
    last_timestamp TEXT NOT NULL
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_elec_interval ON electricity_readings(interval_start);
CREATE INDEX IF NOT EXISTS idx_elec_source ON electricity_readings(source, interval_start);
CREATE INDEX IF NOT EXISTS idx_temp_sensor ON temperature_readings(sensor_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_sauna_start ON sauna_sessions(start_time);
"""


def get_db_path() -> Path:
    """Get the database path, creating parent directories if needed."""
    db_path = Path(DEFAULT_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


@contextmanager
def get_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Get a database connection with row factory enabled."""
    path = db_path or get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    """Initialize the database schema."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def get_stats(db_path: Path | None = None) -> dict:
    """Get database statistics."""
    with get_connection(db_path) as conn:
        stats = {}

        # Electricity readings
        row = conn.execute(
            "SELECT COUNT(*) as count, MIN(interval_start) as earliest, MAX(interval_start) as latest FROM electricity_readings"
        ).fetchone()
        stats["electricity_readings"] = {
            "count": row["count"],
            "earliest": row["earliest"],
            "latest": row["latest"],
        }

        # By source
        rows = conn.execute(
            "SELECT source, COUNT(*) as count FROM electricity_readings GROUP BY source"
        ).fetchall()
        stats["electricity_by_source"] = {row["source"]: row["count"] for row in rows}

        # Temperature readings
        row = conn.execute(
            "SELECT COUNT(*) as count, MIN(timestamp) as earliest, MAX(timestamp) as latest FROM temperature_readings"
        ).fetchone()
        stats["temperature_readings"] = {
            "count": row["count"],
            "earliest": row["earliest"],
            "latest": row["latest"],
        }

        # Sauna sessions
        row = conn.execute("SELECT COUNT(*) as count FROM sauna_sessions").fetchone()
        stats["sauna_sessions"] = {"count": row["count"]}

        # Tariffs
        row = conn.execute("SELECT COUNT(*) as count FROM tariffs").fetchone()
        stats["tariffs"] = {"count": row["count"]}

        return stats
