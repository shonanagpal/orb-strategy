# orb_strategy/strangle_config.py
#
# All parameters for the ORB Short Strangle strategy.
# Completely isolated from config.py — no shared state.
#
# Backtested: Jan 2020 – Mar 2026 (588 days, 87.2% WR, max DD Rs.-10,098)
# Filters: Mon/Tue/Fri only, ATR < 0.25%, 1% OTM strikes

from datetime import time

# ── Paths ─────────────────────────────────────────────────────────────────────
LOG_DIR  = "/home/shona_nagpal/trading/Bollinger/production/orb_strategy/logs"
LOG_FILE = f"{LOG_DIR}/strangle_strategy.log"

# ── Entry timing ──────────────────────────────────────────────────────────────
STRANGLE_ENTRY_TIME = time(10, 0)   # enter at 10:00 AM bar close
STRANGLE_EXIT_TIME  = time(15, 0)   # hard square-off at 15:00

# ── ORB window (to check containment) ────────────────────────────────────────
ORB_WINDOW_MIN = 30    # 9:15 → 9:45

# ── Entry filters ─────────────────────────────────────────────────────────────
# Pre  Sep 2025 (Thu expiry): Wednesday was the problem day (day before expiry)
# Post Sep 2025 (Tue expiry): Monday is the problem day (day before expiry)
# Unified rule: trade Tue–Fri, skip Monday in all regimes going forward
TRADE_WEEKDAYS  = {1, 2, 3, 4}   # Tue=1 Wed=2 Thu=3 Fri=4 (skip Mon=0)
ATR_MAX_PCT     = 0.25         # skip day if 5M ATR % at 10am exceeds this
ATR_PERIOD      = 14           # ATR lookback (5M bars)

# ── Strike placement ──────────────────────────────────────────────────────────
STRIKE_OFFSET_PCT = 1.0        # sell CE/PE this % away from spot
STRIKE_STEP       = 50         # Nifty strike ladder

# ── Position sizing ───────────────────────────────────────────────────────────
LOTS     = 2                   # fixed 2 lots per strangle
LOT_SIZE = 65                  # Nifty lot size (current as of 2025)
# Note: margin required ~Rs.1.4–1.6L for 2-lot short strangle

# ── Adjustment rules ──────────────────────────────────────────────────────────
MAX_ADJUSTMENTS = 2            # max re-entries after initial strangle

# ── Transaction costs ─────────────────────────────────────────────────────────
COST_PER_CYCLE = 300           # flat Rs. per cycle (open + close both legs, Zerodha)

# ── Execution ─────────────────────────────────────────────────────────────────
POLL_INTERVAL_SEC = 5          # seconds between checks (shared with ORB loop)
