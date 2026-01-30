#!/bin/bash

# Navigate to script directory
cd "$(dirname "$0")"

# Source environment variables
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Activate virtual environment
source venv/bin/activate

echo "Starting nightly update..."

# 1. Import EON data (last 7 days)
echo "Fetching EON data..."
eonapi export --days 7 > consumption.csv
energy import eon --csv consumption.csv
rm consumption.csv

# 2. Import Huum data (current month)
echo "Fetching Huum data..."
huum statistics --all > sauna-stats.txt
energy import huum --file sauna-stats.txt
rm sauna-stats.txt

# 3. Import Shelly data (last 7 days)
echo "Fetching Shelly data..."
energy import shelly-csv --days 7

# 4. Import Weather data (last 7 days)
echo "Fetching Weather data..."
energy import weather --days 7

# 5. Update costs
echo "Updating costs..."
energy tariff update-costs

echo "Update complete."
