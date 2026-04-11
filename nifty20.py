#!/usr/bin/env python3
# bootstrap_nifty50.py
#
# One-time script to seed NIFTY50.parquet with 20 days of 5-min history.
# Run AFTER market close (after 15:30 IST) so today's full day is included.
#
# After this runs:
#   - assembled_candles/NIFTY50.parquet has 1,500 bars (20 days × 75 candles)
#   - Housekeeping will maintain it at 1,500 candles going forward
#   - ORB strategy has enough history for 20D EMA regime filter from day 1
#
# Usage:
#   cd ~/trading/Bollinger
#   source venv/bin/activate
#   source .env.kite
#   python3 bootstrap_nifty50.py

import os
import sys
import datetime
import polars as pl
import pandas as pd
from kiteconnect import KiteConnect

# ── Config ────────────────────────────────────────────────────────────────────
NIFTY_TOKEN   = 256265
ASSEMBLED_DIR = "assembled_candles"
OUTPUT_FILE   = os.path.join(ASSEMBLED_DIR, "NIFTY50.parquet")
TRADING_DAYS  = 22      # fetch 22 calendar-adjusted days to get 20 trading days
INTERVAL      = "5minute"

# ── Kite connect ──────────────────────────────────────────────────────────────
api_key      = os.environ.get("KITE_API_KEY", "")
access_token = os.environ.get("KITE_ACCESS_TOKEN", "")

if not api_key or not access_token:
    print("❌ KITE_API_KEY / KITE_ACCESS_TOKEN not set")
    print("   Run: source .env.kite")
    sys.exit(1)

kite = KiteConnect(api_key=api_key)
kite.set_access_token(access_token)
print(f"✅ Logged in as: {kite.profile()['user_name']}")

# ── Date range ────────────────────────────────────────────────────────────────
end_date   = datetime.date.today()
start_date = end_date - datetime.timedelta(days=TRADING_DAYS + 7)  # buffer for weekends/holidays
print(f"📅 Fetching 5-min Nifty data: {start_date} → {end_date}")

# ── Download ──────────────────────────────────────────────────────────────────
try:
    records = kite.historical_data(
        instrument_token = NIFTY_TOKEN,
        from_date        = str(start_date),
        to_date          = str(end_date),
        interval         = INTERVAL,
        continuous       = False,
        oi               = False,
    )
except Exception as e:
    print(f"❌ Kite fetch failed: {e}")
    sys.exit(1)

if not records:
    print("❌ No data returned from Kite")
    sys.exit(1)

print(f"📊 Downloaded {len(records)} raw bars")

# ── Clean ─────────────────────────────────────────────────────────────────────
df = pd.DataFrame(records)
df["date"] = pd.to_datetime(df["date"])

# Strip timezone
if df["date"].dt.tz is not None:
    df["date"] = df["date"].dt.tz_localize(None)

df = df.sort_values("date").reset_index(drop=True)

# Keep only market hours (9:15 to 15:30)
df = df[
    (df["date"].dt.time >= datetime.time(9, 15)) &
    (df["date"].dt.time <= datetime.time(15, 30))
]

# Keep last 1500 candles (20 trading days × 75 candles)
KEEP = 1500
if len(df) > KEEP:
    df = df.tail(KEEP).reset_index(drop=True)

print(f"📊 After cleaning: {len(df)} bars")
print(f"   From : {df['date'].iloc[0]}")
print(f"   To   : {df['date'].iloc[-1]}")
print(f"   Days : {df['date'].dt.normalize().nunique()}")

# ── Write parquet (polars format to match assembler) ──────────────────────────
os.makedirs(ASSEMBLED_DIR, exist_ok=True)

# Rename columns to match assembler output format
df = df.rename(columns={"date": "datetime"})

# Backup existing file if present
if os.path.exists(OUTPUT_FILE):
    backup = OUTPUT_FILE.replace(".parquet", "_backup.parquet")
    os.rename(OUTPUT_FILE, backup)
    print(f"📦 Existing file backed up → {backup}")

# Write as polars parquet (same format as assembler)
pl_df = pl.from_pandas(df)
pl_df.write_parquet(OUTPUT_FILE)

size_kb = os.path.getsize(OUTPUT_FILE) // 1024
print(f"\n✅ Written → {OUTPUT_FILE} ({size_kb} KB)")
print(f"   {len(df)} bars | {df['datetime'].iloc[0]} → {df['datetime'].iloc[-1]}")
print(f"\n🚀 NIFTY50.parquet is ready. Start the system with:")
print(f"   ./start_all.sh paper")
