# Shelly Cloud API Integration Plan

## Problem Statement

The Shelly 3EM Pro (MAC: FCE8C0D856CC) is connected to Shelly Cloud (visible in app), but doesn't appear in the `energy shelly list-devices` output. Only one device shows up (30c6f782a228 - a Shelly Plus 2PM).

## Current Situation

- **Device**: Shelly 3EM Pro (SPEM-003CEBEU)
- **MAC Address**: FCE8C0D856CC
- **Cloud Status**: Connected (shown in app)
- **Local Network**: Unreliable/flaky (not suitable for 30-min polling)
- **Channel**: 2 (the only phase with good data)

## Investigation Steps

### 1. Verify Cloud API Access

The device appears in the Shelly app but not via API. Possible reasons:

1. **Different cloud server/region**: The device might be on a different Shelly Cloud server
   - Currently using server 103 (EU)
   - Try other server IDs: 101, 102, 104, etc.

2. **Different account**: Device might be registered to a different Shelly account
   - Verify the account logged into the app matches the API key

3. **Device not shared with API key**: Some devices might need explicit permission
   - Check Shelly Cloud account settings

4. **API endpoint issue**: The `device/all_status` endpoint might not show all devices
   - Try alternative endpoints like `/device/list`

### 2. API Endpoints to Test

```bash
# List all devices (alternative endpoint)
curl -X POST https://shelly-103-eu.shelly.cloud/device/list \
  -H "Content-Type: application/json" \
  -d '{"auth_key": "YOUR_KEY"}'

# Get specific device status by ID (if we know it)
curl -X POST https://shelly-103-eu.shelly.cloud/device/status \
  -H "Content-Type: application/json" \
  -d '{"auth_key": "YOUR_KEY", "id": "fce8c0d856cc"}'

# Try with MAC address as device ID
curl -X POST https://shelly-103-eu.shelly.cloud/device/status \
  -H "Content-Type: application/json" \
  -d '{"auth_key": "YOUR_KEY", "id": "FCE8C0D856CC"}'
```

### 3. Server Region Detection

Try different Shelly Cloud servers:

```bash
# Test each server to see which responds with devices
for server in 101 102 103 104 105; do
  echo "Testing server $server..."
  export SHELLY_SERVER_ID=$server
  energy shelly list-devices
done
```

### 4. Shelly Cloud Statistics API

**Good news**: Historical data IS available in Shelly Cloud! Manual CSV export works with hourly granularity:

Example data format (from cloud export):
```
Consumption
Time	Wh
29/01/2026 14:00	97.85
29/01/2026 15:00	63.03
29/01/2026 16:00	3873.43
...
```

- **Granularity**: Hourly readings (not minute-level)
- **Format**: Wh per hour (can aggregate 2 readings for 30-min intervals if needed)
- **Fields**: Consumption (import) and Total returned (export, all zeros for this device)

Now need to find the API endpoint that provides this same data:

```bash
# Fetch historical data via API
curl -X POST https://shelly-103-eu.shelly.cloud/statistics \
  -H "Content-Type: application/json" \
  -d '{
    "auth_key": "YOUR_KEY",
    "device_id": "fce8c0d856cc",
    "channel": 2,
    "date_from": "2026-01-29T00:00:00Z",
    "date_to": "2026-01-30T00:00:00Z"
  }'

# Alternative: try CSV export endpoint
# Look for endpoints like /device/export or /statistics/export
```

## Implementation Plan

### Phase 1: Find the Device in Cloud
1. ✅ Verify cloud credentials work (we can list one device)
2. ⬜ Try different server IDs to find where 3EM is registered
3. ⬜ Try alternative API endpoints (`/device/list`, `/device/status`)
4. ⬜ Check if MAC address can be used as device ID
5. ⬜ Add debug output to show raw API responses

### Phase 2: Fetch Historical Data
1. ⬜ Test `/statistics` endpoint with correct device ID
2. ⬜ Verify data format and granularity (minute/hourly)
3. ⬜ Implement aggregation to 30-minute intervals
4. ⬜ Test with date range from last week

### Phase 3: Automate Collection
1. ⬜ Create cron job to fetch data daily
2. ⬜ Implement incremental fetch (only new data since last import)
3. ⬜ Add error handling and retry logic
4. ⬜ Set up monitoring/alerting for failed imports

## Benefits of Cloud API

- **Reliable**: No dependency on local network stability
- **Historical data**: Can backfill missing periods
- **Multiple devices**: Can add other Shelly devices easily
- **Remote access**: Works from anywhere, not just local network

## Fallback Options

If cloud API doesn't work:

1. **MQTT**: Shelly devices support MQTT publishing
   - Set up local MQTT broker
   - Configure 3EM to publish to broker
   - Subscribe and log data locally

2. **Webhooks**: Configure device to POST to local endpoint
   - Set up simple Flask/FastAPI receiver
   - Device POSTs data on events/intervals
   - More reliable than polling

3. **Fix local network**:
   - Investigate why connection is flaky
   - Consider static IP or DHCP reservation
   - Check WiFi signal strength

## Progress Summary

### Completed
- ✅ Verified device is connected to cloud (screenshot shows green checkmark)
- ✅ Confirmed historical data exists in cloud (hourly CSV export working)
- ✅ Built local HTTP API collector (works but network too flaky)
- ✅ Created shelly_local.py with Gen 2 3EM Pro support
- ✅ Fixed energy counter reading (em1data:2.total_act_energy)
- ✅ **Found correct cloud server: 136** (not 103 as originally assumed)
- ✅ **Device now visible via API** - `/device/all_status` shows 12 devices including Pro 3EM
- ✅ **Live data working** - `/device/status` returns real-time readings (power, voltage, energy totals)

### Key Findings (2026-01-30)

**Server Discovery**:
- Auth key page shows `https://shelly-136-eu.shelly.cloud` (not 103)
- Server 103 only showed a shared Plus 2PM device from a friend's account
- Server 136 shows all 12 home devices including the Pro 3EM

**Device Confirmed**:
```
ID: fce8c0d856cc
Model: SPEM-003CEBEU (Shelly Pro 3EM)
MAC: FCE8C0D856CC
Channel 2 live data:
  - act_power: 64.2W
  - voltage: 235.9V
  - total_act_energy: 18,188,194 Wh (18.2 MWh lifetime)
```

**API Configuration for .env**:
```bash
SHELLY_SERVER_ID=136  # Add this!
SHELLY_DEVICE_ID=fce8c0d856cc
```

### Still Investigating
- ⬜ Historical data endpoint - tested various `/statistics/*` paths, all return 404
- ⬜ Need to find the API that powers the app's CSV export feature

### Endpoints Tested

| Endpoint | Result |
|----------|--------|
| `/device/all_status` | ✅ Works - lists all devices |
| `/device/status` | ✅ Works - returns live data |
| `/statistics` | ❌ 404 |
| `/statistics/emeter/consumption` | ❌ 404 |
| `/statistics/relay/consumption` | ❌ 404 |
| `/device/power_consumption` | ❌ 404 |
| `/device/emdata` | ❌ 404 |
| `/emdata/get_data` | ❌ 404 |

## Next Actions

1. ~~Test server regions~~ → **Done: Server 136 is correct**
2. ~~Find device ID~~ → **Done: fce8c0d856cc works**
3. **Locate historical data endpoint**: Continue testing alternative API paths
4. **Check Shelly API docs**: Look for Gen 2+ cloud statistics endpoints
5. **Fallback: CSV import**: Create importer for manual exports if API doesn't work
