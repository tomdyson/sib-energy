# Home Energy Analysis System - Initial Plan

> This was the initial implementation plan. Items marked ✅ are complete, ⏳ are outstanding.

## Overview
A Python-based system to collect, store, and analyze domestic electricity usage data from multiple sources. Designed for Raspberry Pi deployment with cron-based data collection.

## Data Sources
1. ✅ **EON** - Half-hourly smart meter data via existing [eonapi](https://github.com/tomdyson/eonapi) tool
2. ⏳ **Shelly** - Real-time power data from main incomer (one phase of three), via Shelly Cloud API
3. ✅ **Sauna** - Temperature readings via existing [huum-cli](https://github.com/tomdyson/huum-cli) tool (Huum API)

## Database Schema ✅

### Core Tables

```sql
-- Electricity readings (half-hourly from EON, the source of truth)
electricity_readings (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,           -- 'eon', 'shelly_phase1'
    interval_start TEXT NOT NULL,   -- ISO 8601 with timezone
    interval_end TEXT NOT NULL,
    consumption_kwh REAL NOT NULL,
    cost_pence REAL,                -- Calculated from tariff
    UNIQUE(source, interval_start)
)

-- Temperature readings (for sauna and future sensors)
temperature_readings (
    id INTEGER PRIMARY KEY,
    sensor_id TEXT NOT NULL,        -- 'sauna', 'garage', etc.
    timestamp TEXT NOT NULL,
    temperature_c REAL NOT NULL,
    UNIQUE(sensor_id, timestamp)
)

-- Tariff configuration (supports multiple tariff periods)
tariffs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,             -- 'EON Next Flux', 'Octopus Go', etc.
    valid_from TEXT NOT NULL,
    valid_to TEXT                   -- NULL = current tariff
)

tariff_rates (
    id INTEGER PRIMARY KEY,
    tariff_id INTEGER NOT NULL,
    start_time TEXT NOT NULL,       -- '00:00'
    end_time TEXT NOT NULL,         -- '07:00'
    rate_pence_per_kwh REAL NOT NULL,
    days TEXT DEFAULT '*',          -- '*' = all, 'weekdays', 'weekends'
    FOREIGN KEY (tariff_id) REFERENCES tariffs(id)
)

-- Derived: sauna sessions (computed from temperature data)
sauna_sessions (
    id INTEGER PRIMARY KEY,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    duration_minutes INTEGER,
    peak_temperature_c REAL,
    estimated_kwh REAL              -- Optional: correlate with meter data
)
```

## Project Structure ✅

```
home-energy-analysis/
├── pyproject.toml
├── src/
│   └── energy/
│       ├── __init__.py
│       ├── db.py              # ✅ Database connection, schema setup
│       ├── models.py          # ✅ Dataclasses for readings, tariffs
│       ├── tariffs.py         # ✅ Cost calculation logic
│       ├── collectors/
│       │   ├── __init__.py
│       │   ├── eon.py         # ✅ Import from eonapi CSV/existing DB
│       │   ├── shelly.py      # ⏳ Shelly Cloud API client
│       │   └── huum.py        # ✅ Parse huum-cli output files
│       ├── analysis/
│       │   ├── __init__.py
│       │   ├── sessions.py    # ✅ Detect sauna sessions from temp data
│       │   └── summary.py     # ✅ Generate LLM-friendly summaries
│       └── cli.py             # ✅ Command-line interface
├── config/
│   └── tariffs.yaml           # ✅ Tariff definitions
├── scripts/
│   └── cron_collect.sh        # ⏳ Wrapper for cron jobs
└── tests/                     # ⏳ Test suite
```

## Key Components

### 1. Tariff Configuration (YAML) ✅
```yaml
tariffs:
  - name: "EON Flux"
    valid_from: "2024-01-01"
    rates:
      - start: "00:00"
        end: "07:00"
        rate: 7      # pence/kWh (cheap rate)
      - start: "07:00"
        end: "00:00"
        rate: 25     # pence/kWh (standard rate)
```

### 2. CLI Commands
```bash
# Import data
energy import eon --csv /path/to/export.csv      # ✅
energy import eon --since 2024-01-01             # ⏳ Direct API fetch
energy import shelly --days 7                    # ⏳
energy import huum --days 30                     # ⏳ Direct API fetch
energy import huum --file /path/to/sauna.txt     # ✅

# Analysis
energy summary --date 2025-01-15                 # ✅
energy report --days 7                           # ✅
energy report --days 7 --json                    # ✅
energy sessions list                             # ✅
energy sessions detect                           # ✅

# Database
energy database init                             # ✅
energy database stats                            # ✅

# Tariffs
energy tariff load                               # ✅
energy tariff update-costs                       # ✅
```

### 3. Shelly Cloud Integration ⏳
- Use Shelly Cloud API to fetch historical data
- Store as 5-minute or configurable intervals
- Aggregate to half-hourly to align with EON data for comparison

### 4. Sauna Session Detection ⚠️ (works, needs refinement)
Algorithm from temperature data:
1. Detect heating start: temperature rises above 35°C
2. Track "hot" periods above 55°C
3. Session ends when below 40°C for 2+ hours
4. Extract: start time, end time, peak temperature, duration

**Known limitations:**
- Hard to distinguish heating from ambient temperature on warm days
- Session boundaries are approximate (cooling period included in duration)
- No validation against actual electricity spikes

### 5. LLM-Friendly Summaries ✅
Generate structured text/JSON summaries:
```
Daily Summary for 2026-01-28
- Total consumption: 98.96 kWh
- Estimated cost: £18.96
- Cheap-rate usage: 37.78 kWh (38.2%)
- Peak half-hour: 05:30 (5.64 kWh)
- Sauna session: 05:56-12:42, peak 69.0°C
```

## Implementation Order

1. ✅ **Database setup** - Schema, connection helpers, migrations
2. ✅ **EON importer** - Parse existing CSV/DB from eonapi
3. ✅ **Tariff system** - YAML config, cost calculation
4. ✅ **CLI skeleton** - Click-based interface
5. ✅ **Huum collector** - Parse huum-cli export files
6. ⚠️ **Sauna session detection** - Derive sessions from temperature data (works, needs refinement)
7. ⏳ **Shelly collector** - Cloud API integration
8. ✅ **Summary generator** - LLM-friendly output format
9. ⏳ **Cron scripts** - Scheduled collection on Pi
10. ⏳ **Direct API fetching** - Fetch from EON/Huum APIs directly (currently using CLI tool exports)

## Outstanding Work

### High Priority
- [ ] **Shelly Cloud collector** - Integrate with Shelly Cloud API for real-time power monitoring
- [ ] **Cron scripts** - Create `scripts/cron_collect.sh` wrapper for scheduled data collection

### Medium Priority
- [ ] **Refine sauna session detection** - Improve accuracy by:
  - Comparing sauna temp to outdoor temp (via weather API like Open-Meteo) to detect true heating vs ambient warmth
  - Cross-referencing with electricity spikes to confirm heater was running
  - Better end-time detection (distinguish active use from cooldown)
- [ ] **Direct API integration** - Fetch from EON/Huum APIs directly instead of importing CLI exports
- [ ] **Estimated sauna kWh** - Correlate sauna sessions with electricity readings to fill `estimated_kwh`
- [ ] **Tests** - Add pytest test suite

### Low Priority / Future
- [ ] **Anomaly detection** - Flag unusual consumption patterns
- [ ] **Web dashboard** - Simple Flask/FastAPI UI for viewing data
- [ ] **Multiple Shelly devices** - Support monitoring all three phases
- [ ] **Export to Home Assistant** - Integration with existing HA setup

## Dependencies ✅
- Python 3.11+
- click (CLI)
- httpx (async HTTP for Shelly API)
- pyyaml (tariff config)
- sqlite3 (stdlib)
- rich (CLI output formatting)
