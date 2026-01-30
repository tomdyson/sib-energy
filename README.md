# Home Energy Analysis

A Python tool to collect, store, and analyze domestic electricity usage from multiple sources. Designed for Raspberry Pi deployment with cron-based data collection.

## Data Sources

- **EON** - Half-hourly smart meter data via [eonapi](https://github.com/tomdyson/eonapi)
- **Huum Sauna** - Temperature readings via [huum-cli](https://github.com/tomdyson/huum-cli)
- **Shelly** - 30-minute power monitoring via Shelly Cloud API

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

## Quick Start

```bash
# Initialize the database
energy database init

# Configure tariffs (edit config/tariffs.yaml first)
energy tariff load

# Import electricity data
uvx eonapi export > consumption.csv
energy import eon --csv consumption.csv

# Import sauna temperature data
uvx huum stats --month 2025-01 > sauna.txt
energy import huum --file sauna.txt

# Import Shelly power monitoring data (requires setup, see config/shelly-setup.md)
energy import shelly --days 7

# Detect sauna sessions from temperature data
energy sessions detect

# View reports
energy summary --date 2025-01-15
energy report --days 7
energy report --days 30 --json
```

## CLI Commands

```bash
energy database init          # Create database schema
energy database stats         # Show record counts and date ranges

energy import eon --csv FILE     # Import EON electricity data
energy import huum --file FILE   # Import Huum sauna temperatures
energy import shelly [OPTIONS]   # Import Shelly power data (--days N, --from-date, --to-date)

energy shelly list-devices       # List all Shelly devices on your account

energy tariff load            # Load tariffs from config/tariffs.yaml
energy tariff update-costs    # Recalculate costs for existing readings

energy sessions detect        # Detect sauna sessions from temperature data
energy sessions list          # List recent sessions

energy summary --date DATE    # Daily summary
energy report --days N        # Period summary
energy report --days N --json # JSON output for LLM analysis
```

## Configuration

### Tariffs (`config/tariffs.yaml`)

```yaml
tariffs:
  - name: "EON Next Flux"
    valid_from: "2024-01-01"
    rates:
      - start: "00:00"
        end: "07:00"
        rate: 7      # pence/kWh (cheap overnight)
      - start: "07:00"
        end: "00:00"
        rate: 25     # pence/kWh (standard)
```

After editing, reload with: `energy tariff load && energy tariff update-costs`

## Database

SQLite database stored at `~/.local/share/home-energy/energy.db`

### Schema

```sql
-- Half-hourly electricity readings from smart meter
electricity_readings (
    id, source, interval_start, interval_end,
    consumption_kwh, cost_pence
)

-- Temperature sensor readings (sauna, etc.)
temperature_readings (
    id, sensor_id, timestamp, temperature_c
)

-- Detected sauna usage sessions (derived from temperature data)
sauna_sessions (
    id, start_time, end_time, duration_minutes,
    peak_temperature_c, estimated_kwh
)

-- Tariff definitions and time-of-use rates
tariffs (id, name, valid_from, valid_to)
tariff_rates (id, tariff_id, start_time, end_time, rate_pence_per_kwh, days)
```

## LLM Analysis

### Quick Export

```bash
energy report --days 30 --json | pbcopy
# Paste into Claude for analysis
```

### Agent Database Access

Point an LLM agent (like Claude Code) at the database for deeper analysis:

```bash
# Copy the database for analysis
cp ~/.local/share/home-energy/energy.db ./energy-analysis.db
```

Then use this prompt:

---

## Agent Prompt for Energy Data Analysis

```
You have access to a SQLite database containing home energy usage data. The database is at ./energy-analysis.db (or ~/.local/share/home-energy/energy.db).

## Background

This is a three-phase house in the UK. The electricity tariff has cheap overnight rates (midnight to 7am). The household has an electric sauna which is a significant energy consumer.

## Database Schema

### electricity_readings
Half-hourly smart meter data from EON.
- `source`: 'eon' (future: 'shelly_phase1' for real-time monitoring)
- `interval_start`: ISO 8601 timestamp with timezone (e.g., '2026-01-15T05:30:00+00:00')
- `interval_end`: End of 30-minute interval
- `consumption_kwh`: Energy consumed in this interval
- `cost_pence`: Calculated cost based on time-of-use tariff

### temperature_readings
Temperature sensor data, primarily from the sauna.
- `sensor_id`: 'sauna' (future: other sensors)
- `timestamp`: ISO 8601 timestamp
- `temperature_c`: Temperature in Celsius

### sauna_sessions
Detected sauna usage sessions, derived from temperature patterns.
- `start_time`, `end_time`: Session boundaries
- `duration_minutes`: Total session length (including heating and cooldown)
- `peak_temperature_c`: Maximum temperature reached
- `estimated_kwh`: (Future) Correlated electricity usage

### tariffs / tariff_rates
Time-of-use electricity pricing.
- Cheap rate: midnight to 7am
- Standard rate: 7am to midnight

## Analysis Goals

1. **Cost optimization**: How much usage is during cheap vs expensive hours? What could be shifted?
2. **Sauna correlation**: How much electricity does a sauna session consume? Is there a pattern?
3. **Baseline detection**: What's the house's baseload? Are there anomalies?
4. **Usage patterns**: Daily/weekly patterns? Seasonal trends?
5. **Peak identification**: What times have highest consumption? Why?

## Useful Queries to Start

```sql
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

-- Hourly usage patterns (average by hour of day)
SELECT
    CAST(STRFTIME('%H', interval_start) AS INTEGER) as hour,
    ROUND(AVG(consumption_kwh), 3) as avg_kwh
FROM electricity_readings
WHERE source = 'eon'
GROUP BY hour
ORDER BY hour;

-- Sauna sessions with electricity correlation
SELECT
    s.start_time,
    s.duration_minutes,
    s.peak_temperature_c,
    ROUND(SUM(e.consumption_kwh), 2) as session_kwh
FROM sauna_sessions s
LEFT JOIN electricity_readings e ON
    e.interval_start >= s.start_time
    AND e.interval_start <= s.end_time
    AND e.source = 'eon'
GROUP BY s.id
ORDER BY s.start_time DESC;

-- Days with vs without sauna
SELECT
    CASE WHEN s.id IS NOT NULL THEN 'sauna' ELSE 'no sauna' END as day_type,
    COUNT(DISTINCT DATE(e.interval_start)) as days,
    ROUND(AVG(daily.kwh), 2) as avg_daily_kwh
FROM (
    SELECT DATE(interval_start) as day, SUM(consumption_kwh) as kwh
    FROM electricity_readings WHERE source = 'eon'
    GROUP BY DATE(interval_start)
) daily
LEFT JOIN sauna_sessions s ON DATE(s.start_time) = daily.day
LEFT JOIN electricity_readings e ON DATE(e.interval_start) = daily.day
GROUP BY day_type;
```

Please explore this data and provide insights about energy usage patterns, cost optimization opportunities, and any anomalies you discover.
```

---

## Cron Setup (Raspberry Pi)

```cron
# Fetch EON data daily at 6am
0 6 * * * cd /home/pi/home-energy-analysis && ./venv/bin/energy import eon --csv <(uvx eonapi export)

# Generate daily summary
0 7 * * * cd /home/pi/home-energy-analysis && ./venv/bin/energy summary >> /var/log/energy-summary.log
```

## Development

```bash
pip install -e ".[dev]"
ruff check src/
pytest
```
