# Home Energy Analysis

A Python tool to collect, store, and analyze domestic electricity usage from multiple sources. Designed for Raspberry Pi deployment with cron-based data collection.

## Data Sources

- **EON** - Half-hourly smart meter data via [eonapi](https://github.com/tomdyson/eonapi)
- **Huum Sauna** - Temperature readings via [huum-cli](https://github.com/tomdyson/huum-cli)
- **Shelly Pro 3EM** - Per-minute power monitoring via local HTTP API (aggregated to 30-min intervals)
- **Open-Meteo** - Hourly outside temperature from [Open-Meteo Archive API](https://open-meteo.com/) (free, no API key)

## Installation

```bash
git clone https://github.com/tomdyson/sib-energy.git
cd sib-energy
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

## Quick Start

```bash
# Initialize the database
energy database init

# Run the nightly update script manually
./update_db.sh

# Run with debug output if you encounter issues
./update_db.sh --debug

# Configure tariffs (edit config/tariffs.yaml first)
energy tariff load

# Import Shelly Pro 3EM data from local network (last 30 days)
energy import shelly-csv --days 30

# Or with custom options
energy import shelly-csv --ip 192.168.6.124 --channel 2 --days 7

# Import EON smart meter data
uvx eonapi export > consumption.csv
energy import eon --csv consumption.csv

# Import sauna temperature data (requires huum-cli in venv)
pip install huum-cli  # if not already installed
huum statistics --all > sauna-stats.txt
energy import huum --file sauna-stats.txt

# Detect sauna sessions from temperature data
energy sessions detect

# Import outside temperature data (for energy vs weather correlation)
energy import weather --days 30

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
energy import shelly-csv         # Import Shelly Pro 3EM data from local network
                                 # Options: --ip, --channel, --days
energy import weather            # Import outside temperature from Open-Meteo
                                 # Options: --days, --latitude, --longitude

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

This is a three-phase house in the UK. The electricity tariff has cheap overnight rates (midnight to 7am). The household has:
- An electric sauna (significant energy consumer, ~9kW)
- A studio on a dedicated circuit, monitored separately via Shelly Pro 3EM

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

### tariffs / tariff_rates
Time-of-use electricity pricing.
- Cheap rate: midnight to 7am (7p/kWh)
- Standard rate: 7am to midnight (25p/kWh)

## Analysis Goals

1. **Studio impact**: What % of total usage comes from the studio? Which days does it dominate?
2. **Cost optimization**: How much usage is during cheap vs expensive hours? What could be shifted?
3. **Sauna correlation**: The sauna is in the studio - how do sauna sessions affect studio usage?
4. **Weather correlation**: How does outside temperature affect energy consumption? (heating demand)
5. **Baseline detection**: What's the house's baseload? What's the studio's baseload?
6. **Usage patterns**: Daily/weekly patterns? When is studio most active?
7. **Peak identification**: What times have highest consumption? Is it studio-driven?

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

-- Sauna sessions with studio electricity during session
SELECT
    s.start_time,
    s.duration_minutes,
    s.peak_temperature_c,
    ROUND(SUM(e.consumption_kwh), 2) as studio_kwh_during_session
FROM sauna_sessions s
LEFT JOIN electricity_readings e ON
    e.interval_start >= s.start_time
    AND e.interval_start <= s.end_time
    AND e.source = 'shelly_studio_phase'
GROUP BY s.id
ORDER BY s.start_time DESC;
```

Please explore this data and provide insights about:
- When does the studio have an outsized impact on overall energy use?
- Are there opportunities to shift studio usage to cheap overnight hours?
- How predictable is studio usage compared to total house usage?
```

---

## Cron Setup (Raspberry Pi)

```cron
# Run the nightly update script (fetches all data and updates costs)
0 6 * * * /home/tom/sib-energy/update_db.sh >> /var/log/energy-update.log 2>&1

# Generate daily summary
0 7 * * * cd /home/tom/sib-energy && ./venv/bin/energy summary >> /var/log/energy-summary.log
```

## Development

```bash
pip install -e ".[dev]"
ruff check src/
pytest
```
