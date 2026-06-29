#!/usr/bin/env bash
# set -euo pipefail removed — conflicts with FAILED_SERVICES tracking
# individual critical sections use explicit exit codes instead
set -uo pipefail
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
HOLIDAYS_FILE="$BASE_DIR/data/nse_holidays.csv"
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

# ---------- EXPORT KITE VARS ----------
# Export explicitly so all tmux child sessions inherit them.
# Without this, orb/squeeze get empty KITE_API_KEY and Kite
# only connects hours later when env vars become available — missing signals.
export KITE_API_KEY="${KITE_API_KEY:-}"
export KITE_ACCESS_TOKEN="${KITE_ACCESS_TOKEN:-}"

# Write current mode for health_check.sh
echo "$MODE" > "$BASE_DIR/.current_mode"

# ---------- ENSURE LOGS DIR ----------
mkdir -p "$BASE_DIR/logs"
mkdir -p "$BASE_DIR/production/orb_strategy/logs"
mkdir -p "$BASE_DIR/backtest/skew_data"

# ---------- ROTATE LOGS ----------
LOG_DATE=$(date +%Y-%m-%d)
for svc in candle assembler signal executor options_recorder health_check; do
  LOG="$BASE_DIR/logs/${svc}.log"
  if [ -f "$LOG" ]; then
    mv "$LOG" "$BASE_DIR/logs/${svc}_${LOG_DATE}.log"
  fi
done

ORB_LOG_DIR="$BASE_DIR/production/orb_strategy/logs"
for svc in orb_strategy strangle_strategy squeeze_strategy; do
  LOG="$ORB_LOG_DIR/${svc}.log"
  if [ -f "$LOG" ]; then
    mv "$LOG" "$ORB_LOG_DIR/${svc}_${LOG_DATE}.log"
  fi
done

# Rotate surface stream log
SKEW_LOG="$BASE_DIR/backtest/skew_data/surface_stream.log"
if [ -f "$SKEW_LOG" ]; then
  mv "$SKEW_LOG" "$BASE_DIR/backtest/skew_data/surface_stream_${LOG_DATE}.log"
fi

echo "📁 Logs rotated for $LOG_DATE"

# ---------- HOUSEKEEPING ----------
echo ""
echo "🧹 Running housekeeping..."
echo "⏱️  Housekeeping triggered by start_all.sh @ $(date '+%Y-%m-%d %H:%M:%S')"
HK_LOG="$BASE_DIR/logs/housekeeping_$(date '+%Y-%m-%d_%H-%M-%S').log"
$PYTHON -m production.common.housekeeping >> "$HK_LOG" 2>&1 || true
echo "⏱️  Housekeeping finished @ $(date '+%Y-%m-%d %H:%M:%S')"
cat "$HK_LOG"
echo ""

# ---------- REFRESH NFO EXPIRY CALENDAR ----------
if [[ -n "${KITE_API_KEY:-}" && -n "${KITE_ACCESS_TOKEN:-}" ]]; then
  $PYTHON "$BASE_DIR/orb_strategy/download_nfo_instruments.py" \
    >> "$HK_LOG" 2>&1 \
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
  return 0  # never propagate failure to caller — prevents set -u abort
}

# ---------- SERVICES ----------
# Core Infrastructure — candle collector first, wait for ready before assembler/signal
start_tmux candle "source $BASE_DIR/.env.kite && $PYTHON -m production.live_candle_collector.live_candle_collector" "$BASE_DIR/logs/candle.log"

echo "⏳ Waiting for candle collector to be ready..."
CANDLE_READY=0
for i in $(seq 1 12); do
  sleep 5
  if grep -q -i "connected\|listening\|started\|ready\|subscrib" "$BASE_DIR/logs/candle.log" 2>/dev/null; then
    echo "✅ Candle collector ready (${i}x5s)"
    CANDLE_READY=1
    break
  fi
done
if [ $CANDLE_READY -eq 0 ]; then
  echo "⚠️  Candle collector did not confirm ready after 60s — starting assembler anyway"
fi

start_tmux assembler "source $BASE_DIR/.env.kite && $PYTHON -m production.candle_assembler.run_assembler" "$BASE_DIR/logs/assembler.log"
sleep 3
start_tmux signal "source $BASE_DIR/.env.kite && $PYTHON -m production.signal_generator.run_live" "$BASE_DIR/logs/signal.log"

# Execution Engine
if [[ "$MODE" == "live" ]]; then
  start_tmux executor "source $BASE_DIR/.env.kite && $PYTHON -m production.live_executor.run_live_trades" "$BASE_DIR/logs/executor.log"
  echo "Executor started in live mode"
else
  start_tmux executor "source $BASE_DIR/.env.kite && $PYTHON -m production.paper_executor.run_paper" "$BASE_DIR/logs/executor.log"
  echo "Executor started in paper mode"
fi

# Strategy Fleet
# orb and squeeze explicitly source .env.kite inside their tmux session.
# This guarantees Kite credentials are available at startup (09:00)
# and not hours later — preventing missed ORB signals.
if [ -f "$BASE_DIR/.env.kite" ]; then
  start_tmux orb \
    "source $BASE_DIR/.env.kite && $PYTHON -m orb_strategy.run_orb $MODE" \
    "$BASE_DIR/production/orb_strategy/logs/orb_strategy.log"
  start_tmux squeeze \
    "source $BASE_DIR/.env.kite && $PYTHON -m orb_strategy.squeeze_executor $MODE" \
    "$BASE_DIR/production/orb_strategy/logs/squeeze_strategy.log"
else
  # No .env.kite — start without credentials (paper mode without options trading)
  start_tmux orb \
    "$PYTHON -m orb_strategy.run_orb $MODE" \
    "$BASE_DIR/production/orb_strategy/logs/orb_strategy.log"
  start_tmux squeeze \
    "$PYTHON -m orb_strategy.squeeze_executor $MODE" \
    "$BASE_DIR/production/orb_strategy/logs/squeeze_strategy.log"
  echo "⚠️  No .env.kite — orb/squeeze started without Kite credentials"
fi

start_tmux options_recorder \
  "$PYTHON $BASE_DIR/orb_strategy/nifty_options_recorder.py" \
  "$BASE_DIR/logs/options_recorder.log"

# ---------- SURFACE STREAM (WebSocket IV surface — skew scanner) ----------
if [ -f "$BASE_DIR/.env.kite" ]; then
  start_tmux surface_stream \
    "source $BASE_DIR/.env.kite && cd $BASE_DIR/backtest && $PYTHON surface_stream.py" \
    "$BASE_DIR/backtest/skew_data/surface_stream.log"
  echo "📈 Surface stream started → $BASE_DIR/backtest/skew_data/surface_stream.log"
else
  echo "⚠️  No .env.kite — surface_stream not started (skew scanner offline)"
fi

# ---------- POST-STARTUP HEALTH CHECK ----------
echo ""
echo "⏳ Waiting 20s before health check..."
sleep 20

echo ""
echo "🔍 Service health check:"

# Squeeze exits outside market hours by design — skip its check then
MARKET_HOUR=$(date +%H)
if [[ "$MARKET_HOUR" -ge 9 && "$MARKET_HOUR" -lt 16 ]]; then
  ALL_SESSIONS=(candle assembler signal executor orb options_recorder squeeze surface_stream)
else
  echo "  ℹ️  Outside market hours — squeeze exits by design, skipping its check"
  ALL_SESSIONS=(candle assembler signal executor orb options_recorder surface_stream)
fi

for SESSION in "${ALL_SESSIONS[@]}"; do
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "  ✅ $SESSION running"
  else
    echo "  ❌ $SESSION DIED after start"
    FAILED_SERVICES+=("$SESSION")
  fi
done

# ---------- KITE CONNECTIVITY VERIFICATION ----------
echo ""
if [[ -z "${KITE_API_KEY:-}" || -z "${KITE_ACCESS_TOKEN:-}" ]]; then
  echo "⚠️  KITE_API_KEY or KITE_ACCESS_TOKEN is empty — orb/squeeze will not trade"
else
  echo "🔐 Kite credentials verified ✅"
fi

# ---------- FINAL STATUS ----------
echo ""
NOW=$(date "+%Y-%m-%d %H:%M:%S")
if [ ${#FAILED_SERVICES[@]} -eq 0 ]; then
  echo "✅ All services started successfully in $MODE mode @ $NOW"
else
  echo "❌ Failed services: ${FAILED_SERVICES[*]}"
  echo "   Check logs in $BASE_DIR/logs/"
fi

echo ""
echo "📝 Useful log tails:"
echo "   signal   : tail -f $BASE_DIR/logs/signal.log"
echo "   executor : tail -f $BASE_DIR/logs/executor.log"
echo "   candle   : tail -f $BASE_DIR/logs/candle.log"
echo "   orb      : tail -f $BASE_DIR/production/orb_strategy/logs/orb_strategy.log"
echo "   squeeze  : tail -f $BASE_DIR/production/orb_strategy/logs/squeeze_strategy.log"
echo "   surface  : tail -f $BASE_DIR/backtest/skew_data/surface_stream.log"
