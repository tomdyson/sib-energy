#!/bin/bash

# Home Energy Database Update Script
# Runs nightly to collect data from all sources and update the database.
#
# Features:
# - Lock file prevents concurrent runs
# - Automatic retry for network operations
# - Notifications via ntfy.sh on failure
# - Proper cleanup on exit
# - Comprehensive logging

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK_FILE="/tmp/energy-update.lock"
LOG_DIR="/var/log/sib-energy"
LOG_FILE="$LOG_DIR/update.log"
MAX_RETRIES=3
RETRY_DELAY=30

# ntfy.sh configuration - set NTFY_TOPIC in .env or here
# To receive notifications, subscribe to your topic at https://ntfy.sh/YOUR_TOPIC
NTFY_TOPIC="${NTFY_TOPIC:-}"
NTFY_SERVER="${NTFY_SERVER:-https://ntfy.sh}"

# Temp files to clean up
TEMP_FILES=()

# ============================================================================
# Functions
# ============================================================================

log() {
    local level="${2:-INFO}"
    local msg="[$(date +'%Y-%m-%d %H:%M:%S')] [$level] $1"
    echo "$msg"
    # Try to append to log file, silently fail if we can't
    { echo "$msg" >> "$LOG_FILE"; } 2>/dev/null || true
}

log_error() {
    log "$1" "ERROR"
}

log_warn() {
    log "$1" "WARN"
}

notify() {
    local title="$1"
    local message="$2"
    local priority="${3:-default}"
    local tags="${4:-}"

    if [[ -n "$NTFY_TOPIC" ]]; then
        curl -s \
            -H "Title: $title" \
            -H "Priority: $priority" \
            ${tags:+-H "Tags: $tags"} \
            -d "$message" \
            "$NTFY_SERVER/$NTFY_TOPIC" > /dev/null 2>&1 || true
    fi
}

notify_failure() {
    local message="$1"
    notify "Energy Update Failed" "$message" "high" "warning,zap"
    log_error "Notification sent: $message"
}

notify_success() {
    # Only notify on success if NTFY_NOTIFY_SUCCESS is set
    if [[ "${NTFY_NOTIFY_SUCCESS:-}" == "true" ]]; then
        notify "Energy Update Complete" "$1" "low" "white_check_mark,zap"
    fi
}

cleanup() {
    local exit_code=$?

    # Remove temp files
    for f in "${TEMP_FILES[@]+"${TEMP_FILES[@]}"}"; do
        [[ -f "$f" ]] && rm -f "$f"
    done

    # Release lock
    if [[ -f "$LOCK_FILE" ]]; then
        rm -f "$LOCK_FILE"
        log "Lock released"
    fi

    if [[ $exit_code -ne 0 ]]; then
        log_error "Script exited with code $exit_code"
    fi

    exit $exit_code
}

acquire_lock() {
    if [[ -f "$LOCK_FILE" ]]; then
        local lock_pid
        lock_pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "unknown")

        # Check if the process is still running
        if [[ "$lock_pid" != "unknown" ]] && kill -0 "$lock_pid" 2>/dev/null; then
            log_error "Another instance is running (PID: $lock_pid)"
            notify_failure "Script already running (PID: $lock_pid). Skipping this run."
            exit 1
        else
            log_warn "Stale lock file found, removing"
            rm -f "$LOCK_FILE"
        fi
    fi

    echo $$ > "$LOCK_FILE"
    log "Lock acquired (PID: $$)"
}

retry() {
    local cmd="$1"
    local description="$2"
    local attempt=1

    while [[ $attempt -le $MAX_RETRIES ]]; do
        log "Attempting: $description (attempt $attempt/$MAX_RETRIES)"

        if eval "$cmd"; then
            return 0
        fi

        if [[ $attempt -lt $MAX_RETRIES ]]; then
            log_warn "Failed, retrying in ${RETRY_DELAY}s..."
            sleep $RETRY_DELAY
        fi

        ((attempt++))
    done

    log_error "Failed after $MAX_RETRIES attempts: $description"
    return 1
}

register_temp_file() {
    TEMP_FILES+=("$1")
}

# ============================================================================
# Main Script
# ============================================================================

# Set up trap for cleanup
trap cleanup EXIT

# Create log directory if it doesn't exist
if [[ ! -d "$LOG_DIR" ]]; then
    if sudo mkdir -p "$LOG_DIR" 2>/dev/null && sudo chown "$USER:$USER" "$LOG_DIR" 2>/dev/null; then
        log "Created log directory: $LOG_DIR"
    else
        log_warn "Could not create log directory, logging to stdout only"
    fi
fi

# Start from script directory
cd "$SCRIPT_DIR"

# Enable debug mode if passed as argument
if [[ "${1:-}" == "--debug" ]]; then
    set -x
fi

log "=========================================="
log "Starting energy database update"
log "=========================================="

# Acquire lock
acquire_lock

# Source environment variables
if [[ -f .env ]]; then
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
    log "Loaded .env file"
else
    log_warn "No .env file found"
fi

# Re-read NTFY_TOPIC after sourcing .env
NTFY_TOPIC="${NTFY_TOPIC:-}"

# Activate virtual environment
if [[ -f "venv/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source venv/bin/activate
    log "Virtual environment activated"
else
    log_error "Virtual environment not found at venv/bin/activate"
    notify_failure "Virtual environment not found. Run: python3 -m venv venv && pip install -e ."
    exit 1
fi

# Check for required commands
MISSING_CMDS=()
for cmd in eonapi huum energy; do
    if ! command -v "$cmd" &> /dev/null; then
        MISSING_CMDS+=("$cmd")
    fi
done

if [[ ${#MISSING_CMDS[@]} -gt 0 ]]; then
    log_error "Missing commands: ${MISSING_CMDS[*]}"
    notify_failure "Missing commands: ${MISSING_CMDS[*]}. Run: pip install -e ."
    exit 1
fi

log "All required commands found"

# Track overall success
ERRORS=()

# ----------------------------------------------------------------------------
# 1. Import EON data (last 7 days)
# ----------------------------------------------------------------------------
log "Step 1/8: Fetching EON data..."
CONSUMPTION_CSV="$SCRIPT_DIR/consumption.csv"
register_temp_file "$CONSUMPTION_CSV"

if retry "eonapi export --days 7 > '$CONSUMPTION_CSV'" "EON API export"; then
    if [[ -s "$CONSUMPTION_CSV" ]]; then
        if energy import eon --csv "$CONSUMPTION_CSV"; then
            log "EON data imported successfully"
        else
            log_error "Failed to import EON data"
            ERRORS+=("EON import failed")
        fi
    else
        log_warn "EON export returned empty file"
        ERRORS+=("EON export empty")
    fi
else
    ERRORS+=("EON API failed")
fi

# ----------------------------------------------------------------------------
# 2. Import Huum data (current month)
# ----------------------------------------------------------------------------
log "Step 2/8: Fetching Huum sauna data..."
SAUNA_STATS="$SCRIPT_DIR/sauna-stats.txt"
register_temp_file "$SAUNA_STATS"

if retry "huum statistics --all > '$SAUNA_STATS'" "Huum API export"; then
    if [[ -s "$SAUNA_STATS" ]]; then
        if energy import huum --file "$SAUNA_STATS"; then
            log "Huum data imported successfully"
        else
            log_error "Failed to import Huum data"
            ERRORS+=("Huum import failed")
        fi
    else
        log_warn "Huum export returned empty file"
        ERRORS+=("Huum export empty")
    fi
else
    ERRORS+=("Huum API failed")
fi

# ----------------------------------------------------------------------------
# 3. Import Shelly data (last 3 days)
# ----------------------------------------------------------------------------
log "Step 3/8: Fetching Shelly data..."
if retry "energy import shelly-csv --days 3" "Shelly data import"; then
    log "Shelly data imported successfully"
else
    ERRORS+=("Shelly import failed")
fi

# ----------------------------------------------------------------------------
# 4. Import Weather data (last 7 days)
# ----------------------------------------------------------------------------
log "Step 4/8: Fetching Weather data..."
if retry "energy import weather --days 7" "Weather data import"; then
    log "Weather data imported successfully"
else
    ERRORS+=("Weather import failed")
fi

# ----------------------------------------------------------------------------
# 5. Import Airbnb calendar
# ----------------------------------------------------------------------------
log "Step 5/8: Fetching Airbnb calendar..."
if retry "energy import airbnb" "Airbnb calendar import"; then
    log "Airbnb data imported successfully"
else
    ERRORS+=("Airbnb import failed")
fi

# ----------------------------------------------------------------------------
# 6. Import Home Assistant data
# ----------------------------------------------------------------------------
log "Step 6/8: Fetching Home Assistant data..."
if retry "energy import ha --days 3" "Home Assistant import"; then
    log "Home Assistant data imported successfully"
else
    ERRORS+=("HA import failed")
fi

# ----------------------------------------------------------------------------
# 7. Detect sauna sessions
# ----------------------------------------------------------------------------
log "Step 7/8: Detecting sauna sessions..."
if energy sessions detect; then
    log "Sauna sessions detected successfully"
else
    log_error "Failed to detect sauna sessions"
    ERRORS+=("Session detection failed")
fi

# ----------------------------------------------------------------------------
# 8. Update costs
# ----------------------------------------------------------------------------
log "Step 8/8: Updating costs..."
if energy tariff update-costs; then
    log "Costs updated successfully"
else
    log_error "Failed to update costs"
    ERRORS+=("Cost update failed")
fi

# ----------------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------------
log "=========================================="

if [[ ${#ERRORS[@]} -eq 0 ]]; then
    log "Update completed successfully"
    notify_success "All 8 steps completed successfully"
else
    log_error "Update completed with ${#ERRORS[@]} error(s): ${ERRORS[*]}"
    notify_failure "Completed with errors: ${ERRORS[*]}"
    exit 1
fi

log "=========================================="
