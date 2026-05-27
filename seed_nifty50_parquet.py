"""
seed_nifty50_parquet.py
───────────────────────
One-time script to seed assembled_candles/NIFTY50.parquet with historical
5-min Nifty data from the backtest CSV.

Run from the Bollinger root:
    python3 seed_nifty50_parquet.py

Safe to re-run — deduplicates on datetime before writing.
"""

import os
import polars as pl
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────────────────────
CSV_PATH       = "backtest/nifty_option_stratgey/data/nifty_5m_2022_to_date.csv"
ASSEMBLED_DIR  = "assembled_candles"
SYMBOL         = "NIFTY50"
TMP_SUFFIX     = ".tmp"
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parquet_path = os.path.join(ASSEMBLED_DIR, f"{SYMBOL}.parquet")
    tmp_path     = os.path.join(ASSEMBLED_DIR, f".{SYMBOL}{TMP_SUFFIX}")

    print(f"📂 Reading CSV: {CSV_PATH}")
    csv_df = pl.read_csv(
        CSV_PATH,
        try_parse_dates=False,      # we parse manually for safety
    )

    # Normalise datetime column
    csv_df = csv_df.with_columns(
        pl.col("datetime")
          .str.strptime(pl.Datetime("us"), "%Y-%m-%d %H:%M:%S")
          .alias("datetime")
    )

    # Add symbol column to match assembled parquet schema
    csv_df = csv_df.with_columns(
        pl.lit(SYMBOL).alias("symbol")
    )

    # Keep only the columns the assembler expects
    expected_cols = ["symbol", "datetime", "open", "high", "low", "close", "volume"]
    csv_df = csv_df.select(expected_cols)

    # Cast numeric columns to match parquet dtypes
    csv_df = csv_df.with_columns([
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("volume").cast(pl.Int64),
    ])

    print(f"📊 CSV rows: {len(csv_df):,}  |  "
          f"from {csv_df['datetime'].min()} to {csv_df['datetime'].max()}")

    # Load existing parquet if it exists
    if os.path.exists(parquet_path):
        print(f"📂 Reading existing parquet: {parquet_path}")
        existing_df = pl.read_parquet(parquet_path)
        print(f"📊 Existing rows: {len(existing_df):,}  |  "
              f"from {existing_df['datetime'].min()} to {existing_df['datetime'].max()}")

        # Merge — existing parquet wins on duplicates (it has live data)
        merged = (
            pl.concat([csv_df, existing_df])
            .unique(subset=["datetime"], keep="last")
            .sort("datetime")
        )
    else:
        print("⚠️  No existing parquet found — writing CSV data only")
        merged = csv_df.sort("datetime")

    print(f"✅ Merged rows: {len(merged):,}  |  "
          f"from {merged['datetime'].min()} to {merged['datetime'].max()}")

    # Count distinct days
    days = merged.select(
        pl.col("datetime").cast(pl.Date).alias("date")
    )["date"].n_unique()
    print(f"📅 Distinct trading days: {days}")

    if days < 20:
        print(f"⚠️  Warning: only {days} days available — regime EMA needs 20+")
    else:
        print(f"✅ Sufficient history for REGIME_EMA_PERIOD=20")

    # Atomic write
    os.makedirs(ASSEMBLED_DIR, exist_ok=True)
    merged.write_parquet(tmp_path)
    os.replace(tmp_path, parquet_path)
    print(f"✅ Written to {parquet_path}")


if __name__ == "__main__":
    main()
