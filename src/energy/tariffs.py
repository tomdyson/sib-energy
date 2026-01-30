"""Tariff loading and cost calculation."""

import sqlite3
from datetime import datetime, time
from pathlib import Path

import yaml

from .db import get_connection
from .models import Tariff, TariffRate

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "tariffs.yaml"


def load_tariffs_from_yaml(config_path: Path | None = None) -> list[Tariff]:
    """Load tariff definitions from YAML config file."""
    path = config_path or DEFAULT_CONFIG_PATH
    with open(path) as f:
        data = yaml.safe_load(f)

    tariffs = []
    for t in data.get("tariffs", []):
        rates = [
            TariffRate(
                start_time=r["start"],
                end_time=r["end"],
                rate_pence_per_kwh=r["rate"],
                days=r.get("days", "*"),
            )
            for r in t.get("rates", [])
        ]
        tariffs.append(
            Tariff(
                name=t["name"],
                valid_from=datetime.fromisoformat(t["valid_from"]),
                valid_to=datetime.fromisoformat(t["valid_to"]) if t.get("valid_to") else None,
                rates=rates,
            )
        )
    return tariffs


def save_tariffs_to_db(tariffs: list[Tariff], db_path: Path | None = None) -> int:
    """Save tariffs to the database. Returns number of tariffs saved."""
    count = 0
    with get_connection(db_path) as conn:
        for tariff in tariffs:
            cursor = conn.execute(
                "INSERT OR REPLACE INTO tariffs (name, valid_from, valid_to) VALUES (?, ?, ?)",
                (
                    tariff.name,
                    tariff.valid_from.isoformat(),
                    tariff.valid_to.isoformat() if tariff.valid_to else None,
                ),
            )
            tariff_id = cursor.lastrowid

            # Delete old rates for this tariff
            conn.execute("DELETE FROM tariff_rates WHERE tariff_id = ?", (tariff_id,))

            # Insert new rates
            for rate in tariff.rates:
                conn.execute(
                    """INSERT INTO tariff_rates
                       (tariff_id, start_time, end_time, rate_pence_per_kwh, days)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        tariff_id,
                        rate.start_time,
                        rate.end_time,
                        rate.rate_pence_per_kwh,
                        rate.days,
                    ),
                )
            count += 1
        conn.commit()
    return count


def parse_time(time_str: str) -> time:
    """Parse HH:MM string to time object."""
    parts = time_str.split(":")
    return time(int(parts[0]), int(parts[1]))


def time_in_range(check_time: time, start: time, end: time) -> bool:
    """Check if a time falls within a range (handles overnight ranges)."""
    if start <= end:
        return start <= check_time < end
    else:
        # Overnight range (e.g., 23:00 to 07:00)
        return check_time >= start or check_time < end


def get_rate_for_time(
    dt: datetime, tariff: Tariff | None = None, db_path: Path | None = None
) -> float:
    """Get the rate in pence/kWh for a specific datetime."""
    if tariff is None:
        # Load active tariff from database
        with get_connection(db_path) as conn:
            row = conn.execute(
                """SELECT t.id, t.name, t.valid_from, t.valid_to
                   FROM tariffs t
                   WHERE t.valid_from <= ? AND (t.valid_to IS NULL OR t.valid_to > ?)
                   ORDER BY t.valid_from DESC LIMIT 1""",
                (dt.isoformat(), dt.isoformat()),
            ).fetchone()

            if not row:
                raise ValueError(f"No active tariff found for {dt}")

            # Get rates for this tariff
            rates = conn.execute(
                "SELECT start_time, end_time, rate_pence_per_kwh, days FROM tariff_rates WHERE tariff_id = ?",
                (row["id"],),
            ).fetchall()

            tariff = Tariff(
                name=row["name"],
                valid_from=datetime.fromisoformat(row["valid_from"]),
                valid_to=(
                    datetime.fromisoformat(row["valid_to"]) if row["valid_to"] else None
                ),
                rates=[
                    TariffRate(
                        start_time=r["start_time"],
                        end_time=r["end_time"],
                        rate_pence_per_kwh=r["rate_pence_per_kwh"],
                        days=r["days"],
                    )
                    for r in rates
                ],
            )

    check_time = dt.time()
    weekday = dt.weekday()

    for rate in tariff.rates:
        # Check day restriction
        if rate.days == "weekdays" and weekday >= 5:
            continue
        if rate.days == "weekends" and weekday < 5:
            continue

        start = parse_time(rate.start_time)
        end = parse_time(rate.end_time)

        if time_in_range(check_time, start, end):
            return rate.rate_pence_per_kwh

    raise ValueError(f"No rate found for {dt}")


def calculate_cost(consumption_kwh: float, interval_start: datetime, db_path: Path | None = None) -> float:
    """Calculate cost in pence for a given consumption at a specific time."""
    rate = get_rate_for_time(interval_start, db_path=db_path)
    return consumption_kwh * rate


def update_costs_for_readings(db_path: Path | None = None) -> int:
    """Update cost_pence for all readings that don't have it set. Returns count updated."""
    count = 0
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, interval_start, consumption_kwh FROM electricity_readings WHERE cost_pence IS NULL"
        ).fetchall()

        for row in rows:
            dt = datetime.fromisoformat(row["interval_start"])
            try:
                cost = calculate_cost(row["consumption_kwh"], dt, db_path)
                conn.execute(
                    "UPDATE electricity_readings SET cost_pence = ? WHERE id = ?",
                    (cost, row["id"]),
                )
                count += 1
            except ValueError:
                # No tariff found for this time, skip
                pass

        conn.commit()
    return count
