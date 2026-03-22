"""
download_nfo_instruments.py

Downloads all NFO instruments from Kite and saves a clean
Nifty-options-only expiry lookup CSV.

Output: ./data/nifty_expiry_calendar.csv
Columns: expiry (date), day_of_week

Usage:
    cd ~/trading/Bollinger/backtest/nifty_option_stratgey/
    python3 download_nfo_instruments.py
"""

import os
import pandas as pd
from datetime import date

KITE_API_KEY      = os.environ.get("KITE_API_KEY", "")
KITE_ACCESS_TOKEN = os.environ.get("KITE_ACCESS_TOKEN", "")
OUTPUT_PATH       = "./data/nifty_expiry_calendar.csv"


def main():
    if not KITE_API_KEY or not KITE_ACCESS_TOKEN:
        print("[!] Set KITE_API_KEY and KITE_ACCESS_TOKEN env vars first")
        return

    try:
        from kiteconnect import KiteConnect
    except ImportError:
        print("[!] pip install kiteconnect")
        return

    k = KiteConnect(api_key=KITE_API_KEY)
    k.set_access_token(KITE_ACCESS_TOKEN)

    print("[*] Downloading NFO instruments...")
    instruments = k.instruments("NFO")
    df = pd.DataFrame(instruments)

    # Keep only Nifty options
    nifty = df[
        (df["name"] == "NIFTY") &
        (df["instrument_type"].isin(["CE", "PE"]))
    ].copy()

    nifty["expiry"] = pd.to_datetime(nifty["expiry"]).dt.date

    # Unique expiry dates
    expiries = (
        nifty[["expiry"]]
        .drop_duplicates()
        .sort_values("expiry")
        .reset_index(drop=True)
    )
    expiries["day_of_week"] = pd.to_datetime(expiries["expiry"]).dt.strftime("%A")

    os.makedirs("./data", exist_ok=True)
    expiries.to_csv(OUTPUT_PATH, index=False)

    print(f"[*] Saved {len(expiries)} expiry dates → {OUTPUT_PATH}")
    print(expiries.to_string())


if __name__ == "__main__":
    main()
