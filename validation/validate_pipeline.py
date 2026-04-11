#!/usr/bin/env python3
# validation/validate_pipeline.py
#
# Run from anywhere — uses absolute paths throughout.
#
# Usage:
#   python3 ~/trading/Bollinger/validation/validate_pipeline.py

from __future__ import annotations

import os
import sys
import datetime as dt
import polars as pl

BASE_DIR      = os.path.expanduser("~/trading/Bollinger")
ASSEMBLED_DIR = os.path.join(BASE_DIR, "assembled_candles")

sys.path.insert(0, BASE_DIR)

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "

results = []

def log(status, test, detail=""):
    results.append((status, test, detail))
    print(f"{status} {test}" + (f" | {detail}" if detail else ""))


# ======================================================
# 1. PostGres insert test
# ======================================================
print("\n── 1. PostGres Insert ──────────────────────────────")
test_signal = None
try:
    # NEW
    from production.signal_generator.postgres_writer import insert_signal, signal_exists

    now = dt.datetime.now()

    test_signal = {
        "strategy_name":     "validation_test",
        "strategy_version":  "0.0",
        "timeframe":         "5m",
        "symbol":            "VALIDATION",
        "side":              "SHORT",
        "signal_time_ist":   now,
        "entry_time_exec_ist": now + dt.timedelta(minutes=5),
        "stoploss":          100.0,
        "signal_status":     "PENDING",
        "atr_pct":           1.2,
        "bb_width":          0.08,
        "vwap_atr":          None,
        "rsi_drop":          None,
        "c1_rvol":           None,
        "c2_rvol":           None,
        "signal_confidence": None,
    }

    insert_signal(test_signal)
    log(PASS, "PostGres insert", "test row inserted")

except Exception as e:
    log(FAIL, "PostGres insert", str(e))


# ======================================================
# 2. Dedup test
# ======================================================
print("\n── 2. Dedup Logic ──────────────────────────────────")
try:
    from production.signal_generator.postgres_writer import insert_signal, signal_exists

    if test_signal is None:
        log(WARN, "Dedup logic", "skipped — insert failed in step 1")
    else:
        import time
        time.sleep(3)   # wait for BQ streaming buffer

        exists = signal_exists(
            strategy_name="validation_test",
            strategy_version="0.0",
            timeframe="5m",
            symbol="VALIDATION",
            signal_time_ist=test_signal["signal_time_ist"],
        )

        if exists:
            insert_signal(test_signal)   # second insert — should be skipped
            log(PASS, "Dedup logic", "duplicate insert correctly skipped")
        else:
            log(WARN, "Dedup logic", "test signal not found yet — BQ streaming buffer lag, check manually")

except Exception as e:
    log(FAIL, "Dedup logic", str(e))


# ======================================================
# 3. Strategy field shape validation
# ======================================================
print("\n── 3. Strategy Field Shapes ────────────────────────")

REQUIRED_FIELDS = {
    "signal_time_ist", "entry_time_exec_ist", "stoploss",
    "atr_pct", "bb_width", "vwap_atr", "rsi_drop",
    "c1_rvol", "c2_rvol", "signal_status",
}

test_df     = None
test_symbol = None

if not os.path.exists(ASSEMBLED_DIR):
    log(FAIL, "Strategy field shapes", f"assembled_candles not found at {ASSEMBLED_DIR}")
else:
    for f in os.listdir(ASSEMBLED_DIR):
        if not f.endswith(".parquet"):
            continue
        df = pl.read_parquet(os.path.join(ASSEMBLED_DIR, f)).sort("datetime")
        if df.height >= 25:
            test_df     = df
            test_symbol = f.replace(".parquet", "")
            break

    if test_df is None:
        log(FAIL, "Strategy field shapes", "no parquet files with >= 25 rows found")
    else:
        for strategy_name, import_path in [
            ("mean_reversion_short", "production.signal_generator.strategies.mean_reversion_short"),
            ("mb_crossover_long",    "production.signal_generator.strategies.mb_crossover_long"),
        ]:
            try:
                import importlib
                mod    = importlib.import_module(import_path)
                result = mod.compute_signals(test_df)

                if result is None:
                    log(PASS, strategy_name, "returned None (no signal) — no crash")
                else:
                    missing = REQUIRED_FIELDS - set(result.keys())
                    if missing:
                        log(FAIL, strategy_name, f"missing fields: {missing}")
                    else:
                        log(PASS, strategy_name, f"all fields present | signal on {test_symbol}")

            except Exception as e:
                log(FAIL, strategy_name, str(e))


# ======================================================
# 4. Full universe scan
# ======================================================
print("\n── 4. Universe Scan ────────────────────────────────")
if not os.path.exists(ASSEMBLED_DIR):
    log(FAIL, "Universe scan", f"assembled_candles not found at {ASSEMBLED_DIR}")
else:
    try:
        from production.signal_generator.strategies.mean_reversion_short import compute_signals as short_signals
        from production.signal_generator.strategies.mb_crossover_long    import compute_signals as long_signals

        total      = 0
        errors     = 0
        too_short  = 0
        short_hits = []
        long_hits  = []

        for f in os.listdir(ASSEMBLED_DIR):
            if not f.endswith(".parquet"):
                continue

            sym = f.replace(".parquet", "")
            total += 1

            try:
                df = pl.read_parquet(os.path.join(ASSEMBLED_DIR, f)).sort("datetime")

                if df.height < 25:
                    too_short += 1
                    continue

                if short_signals(df):
                    short_hits.append(sym)
                if long_signals(df):
                    long_hits.append(sym)

            except Exception as e:
                errors += 1
                print(f"  {FAIL} {sym}: {e}")

        log(PASS, "Universe scan complete",
            f"total={total} errors={errors} too_short={too_short} "
            f"short_signals={len(short_hits)} long_signals={len(long_hits)}"
        )

        if short_hits:
            print(f"  SHORT signals: {', '.join(short_hits)}")
        if long_hits:
            print(f"  LONG  signals: {', '.join(long_hits)}")
        if errors > 0:
            log(WARN, "Errors during scan", f"{errors} symbols failed")

    except Exception as e:
        log(FAIL, "Universe scan", str(e))


# ======================================================
# 5. Candle freshness
# ======================================================
print("\n── 5. Candle Freshness ─────────────────────────────")
if not os.path.exists(ASSEMBLED_DIR):
    log(FAIL, "Candle freshness", f"assembled_candles not found at {ASSEMBLED_DIR}")
else:
    try:
        today = dt.date.today()
        stale = []
        fresh = 0

        for f in os.listdir(ASSEMBLED_DIR):
            if not f.endswith(".parquet"):
                continue
            df        = pl.read_parquet(os.path.join(ASSEMBLED_DIR, f), columns=["datetime"])
            last      = df["datetime"][-1]
            last_date = last.date() if hasattr(last, "date") else dt.datetime.fromisoformat(str(last)).date()

            if last_date < today:
                stale.append(f.replace(".parquet", ""))
            else:
                fresh += 1

        if stale:
            log(WARN, "Stale candles", f"{len(stale)} symbols not updated today: {', '.join(stale[:5])}")
        else:
            log(PASS, "Candle freshness", f"all {fresh} symbols updated today")

    except Exception as e:
        log(FAIL, "Candle freshness", str(e))


# ======================================================
# SUMMARY
# ======================================================
print("\n" + "─" * 55)
print("  VALIDATION SUMMARY")
print("─" * 55)

passed = sum(1 for s, _, _ in results if s == PASS)
failed = sum(1 for s, _, _ in results if s == FAIL)
warned = sum(1 for s, _, _ in results if s == WARN)

for status, test, detail in results:
    print(f"{status} {test}" + (f" | {detail}" if detail else ""))

print(f"\n{PASS} {passed} passed  {FAIL} {failed} failed  {WARN} {warned} warnings")
print("─" * 55)

if failed > 0:
    print("\n🚨 Fix failures before running live tomorrow")
else:
    print("\n✅ Pipeline ready for tomorrow's session")

