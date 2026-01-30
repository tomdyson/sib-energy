# Shelly Cloud Setup Guide

## 1. Get Your Shelly Cloud API Credentials

1. Go to [control.shelly.cloud](https://control.shelly.cloud/) and log in
2. Click your profile/email in the top right corner
3. Go to **User Settings**
4. Find the **Authorization Cloud Key** section
5. Generate a new key if needed, or copy your existing key
6. **Important**: Keep this key secure!

## 2. Find Your Device ID

Once you have your auth key, find your Shelly 3EM device ID:

```bash
# Set your auth key temporarily
export SHELLY_AUTH_KEY='your-auth-key-here'

# List all your devices
energy shelly list-devices
```

This will show a table with all your Shelly devices including their IDs.

## 3. Set Environment Variables

Add these to your `~/.bashrc`, `~/.zshrc`, or create a `.env` file:

```bash
# Required
export SHELLY_AUTH_KEY='your-auth-key-from-step-1'
export SHELLY_DEVICE_ID='your-device-id-from-step-2'

# Optional (default is 103 for EU servers)
export SHELLY_SERVER_ID='103'
```

**For Raspberry Pi cron jobs**, add the exports to your crontab or create a script:

```bash
#!/bin/bash
# /home/pi/home-energy-analysis/fetch-shelly.sh

export SHELLY_AUTH_KEY='your-key'
export SHELLY_DEVICE_ID='your-device-id'

cd /home/pi/home-energy-analysis
./venv/bin/energy import shelly
```

## 4. Test the Integration

```bash
# Fetch last 7 days of data
energy import shelly --days 7

# Or fetch a specific date range
energy import shelly --from-date 2026-01-15 --to-date 2026-01-30

# Subsequent runs will automatically fetch since the last reading
energy import shelly
```

## 5. Verify Data

```bash
# Check database stats
energy database stats

# View recent data
energy report --days 7
```

## Cron Schedule (Raspberry Pi)

Add to your crontab (`crontab -e`):

```cron
# Fetch Shelly data every 2 hours
0 */2 * * * /home/pi/home-energy-analysis/fetch-shelly.sh >> /var/log/shelly-import.log 2>&1
```

## Data Format

- **Source**: `shelly_phase1` in the database
- **Resolution**: 30-minute intervals (aggregated from minute-level Shelly data)
- **Units**: kWh per 30-minute interval
- **Timestamps**: ISO 8601 with timezone
- **Cost**: Automatically calculated based on your tariff configuration

## Troubleshooting

### "SHELLY_AUTH_KEY environment variable not set"
- Make sure you've exported the variable in your current shell
- For cron jobs, the variables must be set in the script itself

### "SHELLY_DEVICE_ID environment variable not set"
- Run `energy shelly list-devices` to find your device ID
- Export it: `export SHELLY_DEVICE_ID='your-id'`

### "Shelly API error"
- Check your auth key is correct
- Verify your device is online in Shelly Cloud
- Try changing `SHELLY_SERVER_ID` if you're on a different server region

### No data imported
- Shelly Cloud only stores data for a limited time (usually 30 days)
- Check the date range you're requesting
- Verify your device has been collecting data during that period
