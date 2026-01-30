#!/bin/bash

# Exit on any error
set -e

# Start from script directory
cd "$(dirname "$0")"

# Enable debug mode if passed as argument
if [[ "$1" == "--debug" ]]; then
    set -x
fi

# Function to log messages
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"
}

# Source environment variables
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Activate virtual environment
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
else
    log "ERROR: Virtual environment not found. Please run 'python3 -m venv venv && source venv/bin/activate && pip install -e .'"
    exit 1
fi

# Check for required commands
for cmd in eonapi huum energy; do
    if ! command -v $cmd &> /dev/null; then
        log "ERROR: Command '$cmd' not found. Please run 'pip install -e .'"
        exit 1
    fi
done

log "Starting nightly update..."

# 1. Import EON data (last 7 days)
log "Fetching EON data..."
eonapi export --days 7 > consumption.csv
log "Importing EON data..."
energy import eon --csv consumption.csv
rm consumption.csv

# 2. Import Huum data (current month)
log "Fetching Huum data..."
huum statistics --all > sauna-stats.txt
log "Importing Huum data..."
energy import huum --file sauna-stats.txt
rm sauna-stats.txt

# 3. Import Shelly data (last 7 days)
log "Fetching Shelly data..."
energy import shelly-csv --days 7

# 4. Import Weather data (last 7 days)
log "Fetching Weather data..."
energy import weather --days 7

# 5. Detect sauna sessions (requires Huum and Weather data)
log "Detecting sauna sessions..."
energy sessions detect

# 5. Update costs
log "Updating costs..."
energy tariff update-costs

log "Update complete."
