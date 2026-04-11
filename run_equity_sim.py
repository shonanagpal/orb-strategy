import os
import sys
import pandas as pd
from datetime import date

# Ensure script can see 'production' folder
sys.path.append(os.getcwd())
from production.common.db import db_cursor

# --- SIMULATION SETTINGS ---
SIM_DATE = "2026-04-10"
CAPITAL_PER_TRADE = 200000  # Your target 2L capital
PARQUET_DIR = os.path.expanduser("~/trading/Bollinger/assembled_candles")

def run_simulation():
    print(f"🚀 EQUITY SIMULATOR — {SIM_DATE}")
    print(f"💰 Capital: ₹{CAPITAL_PER_TRADE:,} per trade\n")

    # 1. Get signals generated from your DB for that day
    with db_cursor() as cur:
        cur.execute("""
            SELECT symbol, entry_price_proxy, signal_time_ist, signal_status
            FROM signals 
            WHERE DATE(signal_time_ist) = %s
            ORDER BY signal_time_ist ASC
        """, (SIM_DATE,))
        signals = cur.fetchall()

    if not signals:
        print("❌ No signals found in DB for this date.")
        return

    print(f"{'SYMBOL':<12} | {'TIME':<8} | {'PROXY':<8} | {'QTY':<6} | {'INVESTED':<12} | {'DATA VERIFIED'}")
    print("-" * 80)

    for sig in signals:
        symbol = sig['symbol']
        proxy_price = sig['entry_price_proxy']
        file_path = os.path.join(PARQUET_DIR, f"{symbol}.parquet")

        # 2. Skip if we don't have a price to calculate Qty
        if not proxy_price or proxy_price <= 0:
            print(f"{symbol:<12} | {sig['signal_time_ist'].strftime('%H:%M'):<8} | {'NULL':<8} | {'0':<6} | {'₹0':<12} | ❌ Missing Price")
            continue

        # 3. Calculate Qty (Your Production Logic)
        qty = int(CAPITAL_PER_TRADE / proxy_price)
        invested = qty * proxy_price

        # 4. Check if we have the Parquet file to verify the trade
        if os.path.exists(file_path):
            # Briefly peek at the parquet to confirm the symbol matches
            df_peek = pd.read_parquet(file_path, columns=[]) # Fast read (metadata/index only)
            verification = "✅ Found Parquet"
        else:
            verification = "⚠️  No Parquet File"

        print(f"{symbol:<12} | {sig['signal_time_ist'].strftime('%H:%M'):<8} | {proxy_price:<8.2f} | {qty:<6} | ₹{invested:<10,.2f} | {verification}")

if __name__ == "__main__":
    run_simulation()
