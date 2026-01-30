"""Generate the agent prompt for energy data analysis.

Reads tariff configuration from config/tariffs.yaml to dynamically include
current tariff rates in the prompt.

Usage:
    python -m energy.generate_prompt
    # or
    energy prompt
"""

from pathlib import Path

import yaml


def get_config_path() -> Path:
    """Find the tariffs.yaml config file."""
    candidates = [
        Path.cwd() / "config" / "tariffs.yaml",
        Path(__file__).parent.parent.parent.parent / "config" / "tariffs.yaml",
        Path.home() / ".config" / "sib-energy" / "tariffs.yaml",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError("Could not find config/tariffs.yaml")


def load_tariffs() -> dict:
    """Load tariff data from YAML."""
    config_path = get_config_path()
    with open(config_path) as f:
        return yaml.safe_load(f)


def format_tariff_description(tariffs_data: dict) -> str:
    """Format tariff rates for the prompt."""
    lines = []
    for tariff in tariffs_data.get("tariffs", []):
        name = tariff.get("name", "Unknown")
        valid_from = tariff.get("valid_from", "Unknown")
        lines.append(f"**{name}** (from {valid_from}):")
        for rate in tariff.get("rates", []):
            start = rate.get("start", "??:??")
            end = rate.get("end", "??:??")
            pence = rate.get("rate", 0)
            lines.append(f"- {start} to {end}: {pence}p/kWh")
    return "\n".join(lines)


def format_tariff_for_section(tariffs_data: dict) -> str:
    """Format tariff info for the database schema section."""
    lines = []
    for tariff in tariffs_data.get("tariffs", []):
        for rate in tariff.get("rates", []):
            start = rate.get("start", "??:??")
            end = rate.get("end", "??:??")
            pence = rate.get("rate", 0)
            # Describe the rate period
            if start == "00:00" and end == "07:00":
                lines.append(f"- Cheap rate: {start} to {end} ({pence}p/kWh)")
            else:
                lines.append(f"- Standard rate: {start} to {end} ({pence}p/kWh)")
    return "\n".join(lines)


def generate_prompt() -> str:
    """Generate the full agent prompt with dynamic tariff information."""
    tariffs_data = load_tariffs()
    tariff_section = format_tariff_for_section(tariffs_data)

    prompt = f"""You have access to a SQLite database containing home energy usage data. 
    
The database is at ~/.local/share/home-energy/energy.db.

## Background

This is a three-phase house in the UK. The electricity tariff has cheap overnight rates. The household has:
- An electric sauna (significant energy consumer, ~9kW). It is not monitored separately. It is not on the studio circuit.
- A studio on a dedicated circuit, monitored separately via Shelly Pro 3EM

There is an EV, which is connected to the studio circuit. We try to run this during cheap periods as much as possible.

There is a pool, unheated. We run the 1kw pump for 7 hours a day in summer, 6 hours in winter. 
It's scheduled to run during cheap periods. This is on the studio circuit. 

The studio (and its water) is heated by an 11kw electric boiler. 
It's mainly used for Airbnb guests. It's expensive to heat!

Note: the sauna is on the main house circuit, so you can look at (EON total - Studio) 
during sauna sessions to see actual heating patterns and costs.

## Data Sources

**IMPORTANT**: The data has two sources with different meanings:

1. **EON** (`source = 'eon'`): Whole-house smart meter data from the electricity supplier.
   - This is the TOTAL consumption for the entire house.
   - Half-hourly intervals.

2. **Shelly Studio** (`source = 'shelly_studio_phase'`): Per-minute data from a Shelly Pro 3EM monitoring the studio circuit.
   - This is a SUBSET of the total (the studio is part of the house).
   - Aggregated to 30-minute intervals to match EON data.
   - Use this to understand what proportion of total usage is from the studio.

When analyzing consumption:
- Use EON for total house consumption
- Use Shelly to understand studio's share of the total
- Never add them together (that would double-count studio usage)

## Database Schema

### electricity_readings
- `source`: 'eon' (whole house) or 'shelly_studio_phase' (studio circuit only)
- `interval_start`: ISO 8601 timestamp with timezone (e.g., '2026-01-15T05:30:00+00:00')
- `interval_end`: End of 30-minute interval
- `consumption_kwh`: Energy consumed in this interval
- `cost_pence`: Calculated cost based on time-of-use tariff

### airbnb_reservations
- `start_date`: Check-in date (inclusive)
- `end_date`: Check-out date (exclusive)
- `status`: Reservation status
- `guest_name`: Name of guest (if available)

### temperature_readings
Temperature sensor data from multiple sources.
- `sensor_id`: 'sauna' (indoor sauna) or 'outside_temperature' (outdoor weather)
- `timestamp`: ISO 8601 timestamp
- `temperature_c`: Temperature in Celsius

### sauna_sessions
Detected sauna usage sessions, derived from temperature patterns.
- `start_time`, `end_time`: Session boundaries
- `duration_minutes`: Total session length (including heating and cooldown)
- `peak_temperature_c`: Maximum temperature reached
- `estimated_kwh`: (Future) Correlated electricity usage

**Important**: Detected durations include cooldown time when the sauna is not drawing power.
Actual heating is typically 1.5-2.5 hours. To identify active heating periods, look for
(EON - Studio) slots where consumption > 3 kWh (the 9kW heater draws ~4.5 kWh per 30-min slot).

### tariffs / tariff_rates
Time-of-use electricity pricing:
{tariff_section}

## Analysis Goals

1. **Studio impact**: What % of total usage comes from the studio? Which days does it dominate?
2. **Airbnb Correlation**: How does energy usage (especially studio) differ when there are guests vs empty?
   - Calculate avg daily studio kWh when occupied vs unoccupied.
   - Estimate the electricity cost per booking.
3. **Cost optimization**: How much usage is during cheap vs expensive hours? What could be shifted?
4. **Sauna correlation**: How much do Sauna sessions cost? How much more do they cost when the outside temperature is low?
5. **Weather correlation**: How does outside temperature affect energy consumption? (heating demand)
6. **Baseline detection**: What's the house's baseload? What's the studio's baseload?
7. **Usage patterns**: Daily/weekly patterns? When is studio most active?
8. **Peak identification**: What times have highest consumption? Is it studio-driven?

## Key Queries

```sql
-- Studio as percentage of total by day
SELECT
    DATE(e.interval_start) as day,
    ROUND(SUM(e.consumption_kwh), 2) as total_kwh,
    ROUND(SUM(s.consumption_kwh), 2) as studio_kwh,
    ROUND(SUM(s.consumption_kwh) / SUM(e.consumption_kwh) * 100, 1) as studio_percent
FROM electricity_readings e
LEFT JOIN electricity_readings s ON
    DATE(e.interval_start) = DATE(s.interval_start)
    AND TIME(e.interval_start) = TIME(s.interval_start)
    AND s.source = 'shelly_studio_phase'
WHERE e.source = 'eon'
GROUP BY DATE(e.interval_start)
ORDER BY day DESC;

-- Daily totals with cost breakdown
SELECT
    DATE(interval_start) as day,
    ROUND(SUM(consumption_kwh), 2) as kwh,
    ROUND(SUM(cost_pence)/100, 2) as cost_gbp,
    ROUND(SUM(CASE WHEN TIME(interval_start) < '07:00' THEN consumption_kwh ELSE 0 END), 2) as cheap_kwh
FROM electricity_readings
WHERE source = 'eon'
GROUP BY DATE(interval_start)
ORDER BY day DESC;

-- Hourly studio usage pattern
SELECT
    CAST(STRFTIME('%H', interval_start) AS INTEGER) as hour,
    ROUND(AVG(consumption_kwh), 3) as avg_kwh
FROM electricity_readings
WHERE source = 'shelly_studio_phase'
GROUP BY hour
ORDER BY hour;

-- Days when studio exceeded 50% of total
SELECT
    DATE(e.interval_start) as day,
    ROUND(SUM(e.consumption_kwh), 2) as total_kwh,
    ROUND(SUM(s.consumption_kwh), 2) as studio_kwh,
    ROUND(SUM(s.consumption_kwh) / SUM(e.consumption_kwh) * 100, 1) as studio_percent
FROM electricity_readings e
LEFT JOIN electricity_readings s ON
    DATE(e.interval_start) = DATE(s.interval_start)
    AND TIME(e.interval_start) = TIME(s.interval_start)
    AND s.source = 'shelly_studio_phase'
WHERE e.source = 'eon'
GROUP BY DATE(e.interval_start)
HAVING studio_percent > 50
ORDER BY studio_percent DESC;


-- Sauna sessions with main house electricity (EON minus studio)
-- Only counts slots > 3 kWh which indicates active heating
SELECT
    s.start_time,
    s.peak_temperature_c,
    COUNT(*) * 30 as heating_mins,
    ROUND(SUM(e.consumption_kwh - COALESCE(sh.consumption_kwh, 0)), 1) as sauna_kwh,
    ROUND(SUM(
        CASE WHEN TIME(e.interval_start) < '07:00'
             THEN (e.consumption_kwh - COALESCE(sh.consumption_kwh, 0)) * 0.07
             ELSE (e.consumption_kwh - COALESCE(sh.consumption_kwh, 0)) * 0.25
        END), 2) as cost_gbp
FROM sauna_sessions s
LEFT JOIN electricity_readings e ON
    e.interval_start >= s.start_time
    AND e.interval_start < datetime(s.start_time, '+180 minutes')
    AND e.source = 'eon'
    AND (e.consumption_kwh - COALESCE(
        (SELECT sh.consumption_kwh FROM electricity_readings sh
         WHERE sh.interval_start = e.interval_start
         AND sh.source = 'shelly_studio_phase'), 0)) > 3.0
LEFT JOIN electricity_readings sh ON
    e.interval_start = sh.interval_start
    AND sh.source = 'shelly_studio_phase'
GROUP BY s.id
ORDER BY s.start_time DESC;

-- Weather correlation: Daily average temp vs total consumption
SELECT 
    DATE(e.interval_start) as day,
    ROUND(AVG(t.temperature_c), 1) as avg_temp,
    ROUND(SUM(e.consumption_kwh), 2) as total_kwh
FROM electricity_readings e
JOIN temperature_readings t 
    ON DATE(e.interval_start) = DATE(t.timestamp)
    AND t.sensor_id = 'outside_temperature'
WHERE e.source = 'eon'
GROUP BY DATE(e.interval_start)
ORDER BY avg_temp;

-- Airbnb Occupancy vs Studio Usage
-- Note: Checking if a day falls within any reservation
SELECT 
    CASE WHEN r.id IS NOT NULL THEN 'Occupied' ELSE 'Vacant' END as occupancy,
    COUNT(DISTINCT DATE(e.interval_start)) as days,
    ROUND(AVG(daily_kwh), 2) as avg_daily_kwh,
    ROUND(AVG(daily_cost), 2) as avg_daily_cost
FROM (
    SELECT 
        DATE(interval_start) as day, 
        SUM(consumption_kwh) as daily_kwh,
        SUM(cost_pence)/100.0 as daily_cost
    FROM electricity_readings 
    WHERE source = 'shelly_studio_phase'
    GROUP BY DATE(interval_start)
) e
LEFT JOIN airbnb_reservations r ON e.day >= r.start_date AND e.day < r.end_date
GROUP BY occupancy;

-- Energy cost per Airbnb booking
SELECT 
    r.guest_name,
    r.start_date,
    r.end_date,
    ROUND(SUM(e.consumption_kwh), 2) as estimated_kwh,
    ROUND(SUM(e.cost_pence)/100.0, 2) as estimated_cost_gbp
FROM airbnb_reservations r
LEFT JOIN electricity_readings e ON 
    e.source = 'shelly_studio_phase'
    AND DATE(e.interval_start) >= r.start_date 
    AND DATE(e.interval_start) < r.end_date
GROUP BY r.id
ORDER BY r.start_date DESC;
```

Please explore this data and provide insights about:
- When does the studio have an outsized impact on overall energy use?
- Are there opportunities to shift studio usage to cheap overnight hours?
- How predictable is studio usage compared to total house usage?

Then generate a beautiful HTML report with all this data and these 
insights. Create it as a single standalone file (use CDN links to 
Tailwind, graphing libraries etc if necessary) that I can upload to S3.
"""

    return prompt


def main():
    """Print the generated prompt to stdout."""
    print(generate_prompt())


if __name__ == "__main__":
    main()
