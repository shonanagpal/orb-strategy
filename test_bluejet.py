import polars as pl
import datetime as dt
from production.signal_generator.strategies.mean_reversion_short import compute_signals

# Load assembled parquet
df = pl.read_parquet('assembled_candles/BLUEJET.parquet').sort('datetime')

# Test 1: Up to 10:05 candle (should NOT trigger based on live results)
print("=" * 70)
print("TEST 1: Up to 10:05 candle")
print("=" * 70)
df_1005 = df.filter(pl.col('datetime') <= dt.datetime(2026, 2, 19, 10, 5))
result_1005 = compute_signals(df_1005)
if result_1005:
    print("✅ Signal triggered")
    print(f"Signal time: {result_1005['signal_time_ist']}")
else:
    print("❌ No signal (matches live behavior)")

# Test 2: Up to 10:10 candle (should trigger based on live results)
print("\n" + "=" * 70)
print("TEST 2: Up to 10:10 candle")
print("=" * 70)
df_1010 = df.filter(pl.col('datetime') <= dt.datetime(2026, 2, 19, 10, 10))
result_1010 = compute_signals(df_1010)
if result_1010:
    print("✅ Signal triggered")
    print(f"Signal time: {result_1010['signal_time_ist']}")
    print(f"Entry time: {result_1010['entry_time_exec_ist']}")
    print(f"ATR%: {result_1010['atr_pct']:.2f}")
    print(f"BB width: {result_1010['bb_width']:.3f}")
else:
    print("❌ No signal (doesn't match live behavior - BUG!)")
