#!/usr/bin/env bash
set -euo pipefail
SESSIONS=(
  candle
  assembler
  orb
  signal
  executor
  options_recorder
  squeeze
  surface_stream
)
echo "🛑 Stopping trading services..."
for SESSION in "${SESSIONS[@]}"; do
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION"
    echo "⏹️  Stopped $SESSION"
  else
    echo "ℹ️  $SESSION not running"
  fi
done

# Clean up surface_stream PID file if it exists
SKEW_PID="$HOME/trading/Bollinger/backtest/skew_data/surface_stream.pid"
if [ -f "$SKEW_PID" ]; then
  rm -f "$SKEW_PID"
  echo "🧹 Removed surface_stream PID file"
fi

echo "✅ All services stopped"
