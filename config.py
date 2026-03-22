# orb_strategy/config.py
# All confirmed parameters from backtest (2022-2026, 223 trades, 54.7% WR)

from datetime import time

# ── Paths ─────────────────────────────────────────────────────────────────────
ASSEMBLED_CANDLES_DIR = "/home/shona_nagpal/trading/Bollinger/assembled_candles"
NIFTY_PARQUET         = f"{ASSEMBLED_CANDLES_DIR}/NIFTY50.parquet"
LOG_DIR               = "/home/shona_nagpal/trading/Bollinger/production/orb_strategy/logs"
LOG_FILE              = f"{LOG_DIR}/orb_strategy.log"
# VIX sourced live from Kite every poll cycle — no CSV needed in paper/live mode

# ── Nifty instrument ──────────────────────────────────────────────────────────
NIFTY_TOKEN  = 256265
NIFTY_SYMBOL = "NIFTY50"

# ── ORB signal parameters (backtested) ───────────────────────────────────────
ORB_WINDOW_MIN      = 30      # opening range = first 30 min (9:15 to 9:45)
ORB_RANGE_MIN_PCT   = 0.10    # min ORB range as % of spot
ORB_RANGE_MAX_PCT   = 1.00    # max ORB range as % of spot
PRIOR_DAY_RANGE_MAX = 1.50    # prior day high-low % must be < this
ATR_MIN_PCT         = 0.10    # 5M ATR at entry must be > this %
ATR_PERIOD          = 14      # ATR lookback (5M bars)
REGIME_EMA_PERIOD   = 20      # daily EMA period for trend direction

# ── Entry / exit timing ───────────────────────────────────────────────────────
MARKET_OPEN       = time(9, 15)
ORB_END_TIME      = time(9, 45)   # 9:15 + 30 min
ENTRY_CUTOFF_TIME = time(14, 0)   # no new entries after 2pm
EXIT_TIME         = time(15, 15)  # square off all positions

# ── Stop loss ─────────────────────────────────────────────────────────────────
SL_SPOT_PCT = 0.40   # exit when spot moves 0.40% against position

# ── Option instrument ─────────────────────────────────────────────────────────
LOT_SIZE          = 65
STRIKE_STEP       = 50
CAPITAL_PER_TRADE = 200_000   # Rs. base budget per trade
CE_SIZE_MULT      = 0.8       # CE: 80% of capital (weaker edge)
PE_SIZE_MULT      = 1.2       # PE: 120% of capital (stronger edge)

# ── Option pricing (Black-Scholes + VIX IV surface) ───────────────────────────
RISK_FREE       = 0.065
BASE_IV         = 0.14
PUT_SKEW        = 0.03
CALL_SKEW       = 0.01
SHORT_DTE_BUMP  = 0.025

# ── Execution ─────────────────────────────────────────────────────────────────
EXECUTION_MODE    = "PAPER"   # "PAPER" or "LIVE"
POLL_INTERVAL_SEC = 5         # seconds between candle checks
