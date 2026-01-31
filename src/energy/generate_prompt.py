"""Generate the agent prompt for energy data analysis.

Reads tariff configuration from config/tariffs.yaml to dynamically include
current tariff rates in the prompt.

Usage:
    python -m energy.generate_prompt
    # or
    energy prompt
"""

import re
from datetime import date, timedelta
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


def get_diary_path() -> Path | None:
    """Find the diary.md config file."""
    candidates = [
        Path.cwd() / "config" / "diary.md",
        Path(__file__).parent.parent.parent.parent / "config" / "diary.md",
        Path.home() / ".config" / "sib-energy" / "diary.md",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def load_diary_entries(days: int = 60) -> list[tuple[date, str]]:
    """Load diary entries from the last N days.

    Returns a list of (date, description) tuples, sorted by date descending.
    """
    diary_path = get_diary_path()
    if not diary_path:
        return []

    cutoff = date.today() - timedelta(days=days)
    entries = []
    date_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(.+)$")

    with open(diary_path) as f:
        for line in f:
            line = line.strip()
            match = date_pattern.match(line)
            if match:
                try:
                    entry_date = date.fromisoformat(match.group(1))
                    if entry_date >= cutoff:
                        entries.append((entry_date, match.group(2)))
                except ValueError:
                    continue

    return sorted(entries, key=lambda x: x[0], reverse=True)


def format_diary_section(days: int = 60) -> str:
    """Format diary entries for inclusion in the prompt."""
    entries = load_diary_entries(days)
    if not entries:
        return ""

    lines = ["## Recent System Changes", ""]
    lines.append(
        "The following notes describe recent changes that may affect energy patterns:"
    )
    lines.append("")
    for entry_date, description in entries:
        lines.append(f"- **{entry_date}**: {description}")

    return "\n".join(lines)


def generate_prompt() -> str:
    """Generate the full agent prompt with dynamic tariff information."""
    tariffs_data = load_tariffs()
    tariff_section = format_tariff_for_section(tariffs_data)
    diary_section = format_diary_section()

    prompt = f"""You have access to a SQLite database containing home energy usage data. 
    
The database is at ~/.local/share/home-energy/energy.db.

## Background

This is a three-phase house in the UK. The electricity tariff has cheap overnight rates. The household has:
- An electric sauna (significant energy consumer, ~9kW). It is not monitored separately. It is not on the studio circuit.
- A studio on a dedicated circuit, monitored separately via Shelly Pro 3EM

There is an EV, which is connected to the studio circuit. We try to run this during cheap periods as much as possible.

There is a pool, unheated. We run the 1kw pump for 7 hours a day in summer, 6 hours in winter.
It's scheduled to run during cheap periods. This is on the studio circuit.

The studio is heated by an 11kW electric boiler that supplies both radiators (space heating) and a small hot water tank.
It's mainly used for Airbnb guests. It's expensive to heat!

**Interpreting studio circuit patterns**:
- Overnight spikes (00:00-07:00) are EXPECTED - this is EV charging and pool pump running during cheap rates. This is good behaviour, not an anomaly.
- Evening peaks (17:00-19:00) are the boiler running radiators and hot water - this happens at expensive peak rates and is a potential optimization target.
- Midday studio usage should be minimal (~0.1-0.2 kWh/slot) unless guests are present.
- Studio usage correlates strongly with outdoor temperature (colder = more heating demand).

Note: the sauna is on the main house circuit, so you can look at (EON total - Studio)
during sauna sessions to see actual heating patterns and costs.

{diary_section}

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
- `sensor_id`: 'sauna', 'outside_temperature', or 'studio_temperature'
- `timestamp`: ISO 8601 timestamp
- `temperature_c`: Temperature in Celsius

Note: `studio_temperature` data may only be available for recent dates (sensor added later).
Check availability with: `SELECT MIN(timestamp), MAX(timestamp) FROM temperature_readings WHERE sensor_id='studio_temperature'`

### half_hourly_temperature (VIEW)
Aggregated temperature data aligned to 30-minute intervals (averaged).
- `sensor_id`: 'sauna', 'outside_temperature', or 'studio_temperature'
- `interval_start`: Aligned timestamp (e.g., 2026-01-29 10:00:00, 10:30:00)
- `avg_temperature_c`: Average temperature in this slot
- `min_temperature_c`: Minimum temperature in this slot
- `max_temperature_c`: Maximum temperature in this slot

### sauna_sessions
Detected sauna usage sessions, derived from temperature patterns and correlated with electricity data.
- `start_time`, `end_time`: Session boundaries (from temperature sensor)
- `duration_minutes`: Total session length including cooldown (from temperature)
- `peak_temperature_c`: Maximum temperature reached
- `heating_minutes`: Actual active heating time (from electricity analysis)
- `estimated_kwh`: Total electricity consumed during heating
- `cheap_kwh`: kWh consumed during cheap rate (00:00-07:00)
- `peak_kwh`: kWh consumed during peak rate (07:00-24:00)
- `cost_pence`: Calculated electricity cost

**How heating is detected**: Active heating is identified by analyzing (EON - Studio) consumption.
When this exceeds 3 kWh per 30-min slot, the 9kW heater is running (~4.5 kWh/slot at full power).
This is more accurate than temperature-based duration which includes passive cooldown time.

### tariffs / tariff_rates
Time-of-use electricity pricing:
{tariff_section}

## Analysis Goals

1. **Studio impact**: What % of total usage comes from the studio? Which days does it dominate?
2. **Airbnb Correlation**: How does energy usage (especially studio) differ when there are guests vs empty?
   - Calculate avg daily studio kWh when occupied vs unoccupied.
   - Estimate the electricity cost per booking.
3. **Cost optimization**: How much usage is during cheap vs expensive hours? What could be shifted?
   - Focus on PEAK HOUR usage that could move to cheap hours (e.g., evening boiler demand for radiators/hot water)
   - Don't flag overnight studio circuit usage as a problem - that's when EV/pool SHOULD run
4. **Sauna correlation**: How much do Sauna sessions cost? Compare cheap-hour vs peak-hour starts.
5. **Weather correlation**: How does outside temperature affect energy consumption? (heating demand)
6. **Baseline detection**: What's the house's baseload? What's the studio's baseload when idle?
7. **Usage patterns**: Daily/weekly patterns? When is studio most active?
8. **Peak identification**: What times have highest consumption? Is it studio-driven?
   - The evening peak (17:00-19:00) is typically the most expensive period to optimize
9. **Anomaly detection**: Identify days with unusually high usage for their temperature band.
   - Calculate kWh per degree below 15°C as an efficiency metric
   - Compare overnight (00:00-07:00) studio circuit usage between similar days to spot large EV charging events
10. **Studio heat retention**: Analyze the relationship between studio internal temp, outdoor temp, and energy used.
    - Look for the daily heating pattern (evening heating → overnight loss → morning recovery)

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


-- Sauna sessions with cost breakdown (pre-calculated from electricity data)
SELECT
    DATE(start_time) as date,
    TIME(start_time) as start,
    peak_temperature_c,
    heating_minutes,
    estimated_kwh as total_kwh,
    cheap_kwh,
    peak_kwh,
    ROUND(cost_pence / 100, 2) as cost_gbp
FROM sauna_sessions
ORDER BY start_time DESC;

-- Weather correlation: Daily average temp vs total consumption
-- Note: Use subquery for outdoor temp to avoid row multiplication from JOIN
SELECT
    DATE(e.interval_start) as day,
    ROUND((SELECT AVG(temperature_c) FROM temperature_readings t
           WHERE DATE(t.timestamp) = DATE(e.interval_start)
           AND t.sensor_id = 'outside_temperature'), 1) as avg_temp,
    ROUND(SUM(e.consumption_kwh), 2) as total_kwh
FROM electricity_readings e
WHERE e.source = 'eon'
GROUP BY DATE(e.interval_start)
ORDER BY avg_temp;

-- Studio Heating Efficiency: Energy vs Outdoor Temp
-- (How much energy does the studio use per degree of coldness?)
SELECT 
    DATE(e.interval_start) as day,
    ROUND(AVG(t_out.avg_temperature_c), 1) as outdoor_temp,
    ROUND(AVG(t_in.avg_temperature_c), 1) as studio_temp,
    ROUND(SUM(e.consumption_kwh), 2) as studio_kwh
FROM electricity_readings e
LEFT JOIN half_hourly_temperature t_out ON
    e.interval_start = t_out.interval_start
    AND t_out.sensor_id = 'outside_temperature'
LEFT JOIN half_hourly_temperature t_in ON
    e.interval_start = t_in.interval_start
    AND t_in.sensor_id = 'studio_temperature'
WHERE e.source = 'shelly_studio_phase'
GROUP BY DATE(e.interval_start)
ORDER BY outdoor_temp;

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

-- Energy cost per Airbnb booking (past bookings with data only)
SELECT
    r.guest_name,
    r.start_date,
    r.end_date,
    JULIANDAY(r.end_date) - JULIANDAY(r.start_date) as nights,
    ROUND(SUM(e.consumption_kwh), 2) as estimated_kwh,
    ROUND(SUM(e.cost_pence)/100.0, 2) as estimated_cost_gbp
FROM airbnb_reservations r
LEFT JOIN electricity_readings e ON
    e.source = 'shelly_studio_phase'
    AND DATE(e.interval_start) >= r.start_date
    AND DATE(e.interval_start) < r.end_date
WHERE r.end_date <= DATE('now')  -- Only past bookings
GROUP BY r.id
HAVING estimated_kwh > 0  -- Only bookings with electricity data
ORDER BY r.start_date DESC;

-- Sauna session costs: cheap vs peak rate starts
SELECT
    CASE WHEN CAST(STRFTIME('%H', start_time) AS INTEGER) < 7
         THEN 'Cheap (before 7am)' ELSE 'Peak (7am onwards)' END as start_period,
    COUNT(*) as sessions,
    ROUND(AVG(heating_minutes), 0) as avg_heat_mins,
    ROUND(AVG(estimated_kwh), 2) as avg_kwh,
    ROUND(AVG(cost_pence)/100, 2) as avg_cost_gbp,
    ROUND(MIN(cost_pence)/100, 2) as min_cost_gbp,
    ROUND(MAX(cost_pence)/100, 2) as max_cost_gbp
FROM sauna_sessions
GROUP BY start_period;

-- Studio cost by outdoor temperature band
WITH daily_data AS (
    SELECT
        DATE(e.interval_start) as day,
        (SELECT AVG(temperature_c) FROM temperature_readings t
         WHERE DATE(t.timestamp) = DATE(e.interval_start)
         AND t.sensor_id = 'outside_temperature') as outdoor_temp_c,
        SUM(e.consumption_kwh) as studio_kwh,
        SUM(e.cost_pence)/100.0 as studio_cost_gbp
    FROM electricity_readings e
    WHERE e.source = 'shelly_studio_phase'
    GROUP BY DATE(e.interval_start)
)
SELECT
    CASE
        WHEN outdoor_temp_c < 0 THEN 'Below 0°C'
        WHEN outdoor_temp_c < 3 THEN '0-3°C'
        WHEN outdoor_temp_c < 6 THEN '3-6°C'
        ELSE 'Above 6°C'
    END as temp_band,
    COUNT(*) as days,
    ROUND(AVG(studio_kwh), 2) as avg_kwh,
    ROUND(AVG(studio_cost_gbp), 2) as avg_cost_gbp
FROM daily_data
WHERE outdoor_temp_c IS NOT NULL
GROUP BY temp_band
ORDER BY MIN(outdoor_temp_c);

-- Anomaly detection: days with high usage for their temperature
-- (kWh per degree below 15°C - higher values are less efficient/anomalous)
WITH daily_data AS (
    SELECT
        DATE(e.interval_start) as day,
        (SELECT AVG(temperature_c) FROM temperature_readings t
         WHERE DATE(t.timestamp) = DATE(e.interval_start)
         AND t.sensor_id = 'outside_temperature') as outdoor_temp_c,
        SUM(e.consumption_kwh) as studio_kwh
    FROM electricity_readings e
    WHERE e.source = 'shelly_studio_phase'
    GROUP BY DATE(e.interval_start)
)
SELECT
    day,
    ROUND(outdoor_temp_c, 1) as temp_c,
    ROUND(studio_kwh, 2) as kwh,
    ROUND(studio_kwh / (15 - outdoor_temp_c), 2) as kwh_per_degree_below_15c
FROM daily_data
WHERE outdoor_temp_c IS NOT NULL AND outdoor_temp_c < 15
ORDER BY kwh_per_degree_below_15c DESC
LIMIT 10;
```

Please explore this data and provide insights about:
- When does the studio have an outsized impact on overall energy use?
- Are there opportunities to shift PEAK HOUR studio usage (especially evening heating demand) to cheap overnight hours?
- How predictable is studio usage compared to total house usage?
- What's the cost difference between sauna sessions started during cheap vs peak hours?
- How much extra electricity does an Airbnb guest add per day?
- How well does the studio retain heat? (Correlation between inside/outside temp and energy)

**Look specifically for anomalies**:
- Days with unusually high kWh-per-degree-below-15°C ratios (use the anomaly query)
- Large variations in overnight (00:00-07:00) studio usage between similar days (indicates EV charging)
- Studio temperature patterns: if data available, look for the daily heating cycle

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
