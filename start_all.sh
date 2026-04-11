#!/usr/bin/env bash
set -euo pipefail
BASE_DIR="$HOME/trading/Bollinger"
VENV="$BASE_DIR/production/common/venv"
PYTHON="$VENV/bin/python -u"

# ---------- MODE FLAG ----------
MODE="${1:-paper}"
if [[ "$MODE" != "paper" && "$MODE" != "live" ]]; then
  echo "❌ Invalid mode: $MODE"
  echo "👉 Usage: ./start_all.sh [paper|live]"
  exit 1
fi
echo "🚀 Starting in $MODE mode"

cd "$BASE_DIR"

# ---------- ENV ----------
source "$VENV/bin/activate"

# ---------- HOLIDAY CHECK ----------
HOLIDAYS_FILE="$BASE_DIR/orb_strategy/data/nse_holidays.csv"
TODAY=$(date +%Y-%m-%d)

if [ -f "$HOLIDAYS_FILE" ]; then
  HOLIDAY_DESC=$(tail -n +2 "$HOLIDAYS_FILE" | awk -F',' -v today="$TODAY" '$1 == today {print $2}' | tr -d '\r')
  if [ -n "$HOLIDAY_DESC" ]; then
    echo "📅 Market closed today — $TODAY is a holiday: $HOLIDAY_DESC"
    echo "🛑 No services will be started. Exiting."
    exit 0
  fi
else
  echo "⚠️  Holidays file not found at $HOLIDAYS_FILE — skipping holiday check"
fi

# ---------- KITE TOKEN GENERATION ----------
if [[ "$MODE" == "live" ]]; then
  echo ""
  echo "🔐 Refreshing Kite access token..."
  if [ ! -f "$HOME/.kite_secrets" ]; then
    echo "❌ ~/.kite_secrets not found — cannot generate Kite token"
    exit 1
  fi
  if "$VENV/bin/python" "$BASE_DIR/generate_kite_token.py"; then
    echo "✅ Kite token refreshed successfully"
  else
    echo "❌ Kite token generation failed — aborting live mode"
    exit 1
  fi
  echo ""
fi

# ---------- LOAD KITE ENV ----------
if [ -f "$BASE_DIR/.env.kite" ]; then
  source "$BASE_DIR/.env.kite"
  echo "🔐 Kite token loaded"
else
  if [[ "$MODE" == "live" ]]; then
    echo "❌ Missing .env.kite — required for live mode"
    exit 1
  else
    echo "⚠️  No .env.kite found — continuing in paper mode"
  fi
fi

# Write current mode for health_check.sh
echo "$MODE" > "$BASE_DIR/.current_mode"

# ---------- ENSURE LOGS DIR ----------
mkdir -p "$BASE_DIR/logs"
mkdir -p "$BASE_DIR/production/orb_strategy/logs"

# ---------- ROTATE LOGS ----------
LOG_DATE=$(date +%Y-%m-%d)
for svc in candle assembler signal executor options_recorder; do
  LOG="$BASE_DIR/logs/${svc}.log"
  if [ -f "$LOG" ]; then
    mv "$LOG" "$BASE_DIR/logs/${svc}_${LOG_DATE}.log"
  fi
done

# UPDATED: Added squeeze_strategy to the rotation fleet
ORB_LOG_DIR="$BASE_DIR/production/orb_strategy/logs"
for svc in orb_strategy strangle_strategy squeeze_strategy; do
  LOG="$ORB_LOG_DIR/${svc}.log"
  if [ -f "$LOG" ]; then
    mv "$LOG" "$ORB_LOG_DIR/${svc}_${LOG_DATE}.log"
  fi
done
echo "📁 Logs rotated for $LOG_DATE"

# ---------- HOUSEKEEPING ----------
echo ""
echo "🧹 Running housekeeping..."
$PYTHON -m production.common.housekeeping >> "$BASE_DIR/logs/housekeeping_${LOG_DATE}.log" 2>&1
cat "$BASE_DIR/logs/housekeeping_${LOG_DATE}.log"
echo ""

# ---------- REFRESH NFO EXPIRY CALENDAR ----------
if [[ -n "${KITE_API_KEY:-}" && -n "${KITE_ACCESS_TOKEN:-}" ]]; then
  $PYTHON "$BASE_DIR/orb_strategy/download_nfo_instruments.py" \
    >> "$BASE_DIR/logs/housekeeping_${LOG_DATE}.log" 2>&1 \
    && echo "✅ Expiry calendar refreshed" \
    || echo "⚠️  Expiry calendar refresh failed"
fi

# ---------- tmux helper ----------
FAILED_SERVICES=()
start_tmux() {
  local SESSION="$1"
  local CMD="$2"
  local LOG="$3"
  tmux kill-session -t "$SESSION" 2>/dev/null || true
  if tmux new-session -d -s "$SESSION" "$CMD >> $LOG 2>&1"; then
    echo "▶  Started $SESSION → $LOG"
  else
    echo "❌ Failed to start $SESSION"
    FAILED_SERVICES+=("$SESSION")
  fi
}

# ---------- SERVICES ----------
# Core Infrastructure
start_tmux candle "$PYTHON -m production.live_candle_collector.live_candle_collector" "$BASE_DIR/logs/candle.log"
start_tmux assembler "$PYTHON -m production.candle_assembler.run_assembler" "$BASE_DIR/logs/assembler.log"
sleep 5
start_tmux signal "$PYTHON -m production.signal_generator.run_live" "$BASE_DIR/logs/signal.log"

# Execution Engine
if [[ "$MODE" == "live" ]]; then
  start_tmux executor "$PYTHON -m production.live_executor.run_live_trades" "$BASE_DIR/logs/executor.log"
else
  start_tmux executor "$PYTHON -m production.paper_executor.run_paper" "$BASE_DIR/logs/executor.log"
fi

# Strategy Fleet
start_tmux orb "$PYTHON -m orb_strategy.run_orb $MODE" "$BASE_DIR/production/orb_strategy/logs/orb_strategy.log"
start_tmux options_recorder "$PYTHON $BASE_DIR/orb_strategy/nifty_options_recorder.py" "$BASE_DIR/logs/options_recorder.log"

# UPDATED: Launching the Squeeze Bot
start_tmux squeeze "$PYTHON -m orb_strategy.squeeze_executor $MODE" "$BASE_DIR/production/orb_strategy/logs/squeeze_strategy.log"

# ---------- FINAL STATUS ----------
NOW=$(date "+%Y-%m-%d %H:%M:%S")
if [ ${#FAILED_SERVICES[@]} -eq 0 ]; then
  echo "✅ All services (including Squeeze) started in $MODE mode"
else
  echo "❌ Some services failed: ${FAILED_SERVICES[*]}"
fi

echo ""
echo "📝 Follow Squeeze logs with: tail -f $BASE_DIR/production/orb_strategy/logs/squeeze_strategy.log"
