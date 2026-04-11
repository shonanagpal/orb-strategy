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

echo "✅ All services stopped"

