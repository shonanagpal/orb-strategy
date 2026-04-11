#!/bin/bash
# =============================================================
# refresh_kite_token.sh
# Run this every morning before market open to refresh the token.
# Usage: bash ~/trading/Bollinger/refresh_kite_token.sh
# =============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$SCRIPT_DIR/production/common/venv/bin/python3"
TOKEN_SCRIPT="$SCRIPT_DIR/generate_kite_token.py"
ENV_FILE="$SCRIPT_DIR/.env.kite"
LOG_FILE="$SCRIPT_DIR/logs/kite_token_refresh.log"

mkdir -p "$(dirname "$LOG_FILE")"

echo "============================================" | tee -a "$LOG_FILE"
echo "🕐 $(date '+%Y-%m-%d %H:%M:%S IST') — Starting Kite token refresh" | tee -a "$LOG_FILE"
echo "============================================" | tee -a "$LOG_FILE"

# Run token generator
"$PYTHON" "$TOKEN_SCRIPT" 2>&1 | tee -a "$LOG_FILE"

# Source the updated .env.kite so it's active in this shell session
if [ -f "$ENV_FILE" ]; then
    source "$ENV_FILE"
    echo "✅ .env.kite sourced into current shell" | tee -a "$LOG_FILE"
else
    echo "❌ .env.kite not found at $ENV_FILE" | tee -a "$LOG_FILE"
    exit 1
fi

echo "🎉 Token refresh complete at $(date '+%H:%M:%S')" | tee -a "$LOG_FILE"
