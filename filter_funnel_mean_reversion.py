#!/usr/bin/env python3

from __future__ import annotations

import os
import sys
from datetime import date

import polars as pl

ASSEMBLED_DIR = "assembled_candles"

BB_PERIOD  = 20
BB_STD     = 2.0
RSI_PERIOD = 14
ATR_PERIOD = 14

STRATEGIES = {
    "mean_reversion_short": {
        "bb_width_min": 0.05,
        "atr_pct_min":  1.0,
        "atr_pct_max":  2.0,
        "rsi_min":      55,
        "rsi_max":      80,
        "vwap_atr_max": 1.25,
        "ema5_required": None,
        "time_windows": [(9,30,10,45), (14,0,15,5)],
        "near_bb_min": 0.045,
        "near_atr_low_min": 0.8,
        "near_atr_low_max": 1.0,
        "near_atr_high_min": 2.0,
        "near_atr_high_max": 2.5,
        "near_rsi_low_min": 50,
        "near_rsi_low_max": 55,
        "near_rsi_high_min": 80,
        "near_rsi_high_max": 85,
    },
    "mb_crossover_long": {
        "bb_width_min": 0.05,
        "atr_pct_min":  0.5,
        "atr_pct_max":  1.0,
        "rsi_min":      30,
        "rsi_max":      50,
        "vwap_atr_max": None,
        "ema5_required": "above",
        "time_windows": [(9,45,10,50)],
        "near_bb_min": 0.045,
        "near_atr_low_min": 0.3,
        "near_atr_low_max": 0.5,
        "near_atr_high_min": 1.0,
        "near_atr_high_max": 1.3,
        "near_rsi_low_min": 25,
        "near_rsi_low_max": 30,
        "near_rsi_high_min": 50,
        "near_rsi_high_max": 55,
    },
}


def in_windows(h: int, m: int, windows: list) -> bool:
    return any((sh, sm) <= (h, m) <= (eh, em) for sh, sm, eh, em in windows)


def compute_indicators(df: pl.DataFrame) -> pl.DataFrame | None:
    if df.is_empty() or df.height < 5:
        return None

    close = pl.col("close")
    lf = df.lazy()

    # --- Bollinger Bands ---
    lf = lf.with_columns([
        close.rolling_mean(20).alias("mb"),
        close.rolling_std(20).alias("std"),
    ])

    lf = lf.with_columns([
        (pl.col("mb") + 2 * pl.col("std")).alias("ub"),
        (pl.col("mb") - 2 * pl.col("std")).alias("lb"),
    ])

    lf = lf.with_columns(
        ((pl.col("ub") - pl.col("lb")) / pl.col("mb")).alias("bb_width")
    )

    # --- Delta ---
    lf = lf.with_columns((close - close.shift(1)).alias("delta"))

    # --- RSI (simple) ---
    lf = lf.with_columns([
        pl.when(pl.col("delta") > 0).then(pl.col("delta")).otherwise(0)
        .rolling_mean(14).alias("avg_gain"),

        pl.when(pl.col("delta") < 0).then(-pl.col("delta")).otherwise(0)
        .rolling_mean(14).alias("avg_loss"),
    ])

    lf = lf.with_columns(
        (100 - (100 / (1 + pl.col("avg_gain") / pl.col("avg_loss")))).alias("rsi_simple")
    )

    # --- RSI Wilder ---
    lf = lf.with_columns([
        pl.when(pl.col("delta") > 0).then(pl.col("delta")).otherwise(0)
        .ewm_mean(alpha=1/14, adjust=False).alias("avg_gain_w"),

        pl.when(pl.col("delta") < 0).then(-pl.col("delta")).otherwise(0)
        .ewm_mean(alpha=1/14, adjust=False).alias("avg_loss_w"),
    ])

    lf = lf.with_columns(
        pl.when(pl.col("avg_loss_w") > 0)
        .then(100 - (100 / (1 + pl.col("avg_gain_w") / pl.col("avg_loss_w"))))
        .otherwise(100)
        .alias("rsi_wilder")
    )

    # --- ATR ---
    lf = lf.with_columns(
        pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - close.shift()).abs(),
            (pl.col("low") - close.shift()).abs(),
        ).rolling_mean(14).alias("atr")
    )

    lf = lf.with_columns(
        (pl.col("atr") / close * 100).alias("atr_pct")
    )

    # --- VWAP ---
    lf = lf.with_columns(pl.col("datetime").dt.date().alias("trade_date"))

    lf = lf.with_columns(
        (
            (pl.col("close") * pl.col("volume")).cum_sum().over("trade_date") /
            pl.col("volume").cum_sum().over("trade_date")
        ).alias("vwap")
    )

    lf = lf.with_columns(
        pl.when(pl.col("atr") > 0)
        .then((pl.col("close") - pl.col("vwap")) / pl.col("atr"))
        .otherwise(None)
        .alias("vwap_atr")
    )

    # --- EMA5 ---
    lf = lf.with_columns(close.ewm_mean(span=5, adjust=False).alias("ema5"))
    lf = lf.with_columns((close > pl.col("ema5")).alias("above_ema5"))

    return lf.collect()

def analyse_symbol(path: str, trade_date: date, cfg: dict, rsi_col: str):
    try:
        df = (
            pl.read_parquet(path)
            .sort("datetime")
            .filter(pl.col("datetime").dt.date() == trade_date)
        )

        result = compute_indicators(df)
        if result is None:
            return None

        time_rows = result.filter(
            pl.struct(
                pl.col("datetime").dt.hour().alias("h"),
                pl.col("datetime").dt.minute().alias("m"),
            ).map_elements(
                lambda s: in_windows(s["h"], s["m"], cfg["time_windows"]),
                return_dtype=pl.Boolean,
            )
        )

        if time_rows.is_empty():
            return None

        # --- independent stats ---
        bb_max = float(time_rows["bb_width"].max() or 0)
        atr_max = float(time_rows["atr_pct"].max() or 0)
        atr_min = float(time_rows["atr_pct"].min() or 0)
        rsi_vals = time_rows[rsi_col].drop_nulls()
        rsi_last = float(rsi_vals[-1]) if len(rsi_vals) > 0 else 0

        bb_pass = time_rows.filter(pl.col("bb_width") >= cfg["bb_width_min"]).height > 0
        atr_pass = time_rows.filter(
            (pl.col("atr_pct") >= cfg["atr_pct_min"]) &
            (pl.col("atr_pct") <= cfg["atr_pct_max"])
        ).height > 0
        rsi_pass = time_rows.filter(
            (pl.col(rsi_col) >= cfg["rsi_min"]) &
            (pl.col(rsi_col) <= cfg["rsi_max"])
        ).height > 0

        vwap_pass = True
        if cfg["vwap_atr_max"] is not None:
            vwap_pass = time_rows.filter(pl.col("vwap_atr") <= cfg["vwap_atr_max"]).height > 0

        ema5_pass = True
        if cfg["ema5_required"] == "above":
            ema5_pass = time_rows.filter(pl.col("above_ema5")).height > 0

        # --- ✅ same candle logic ---
        cond = (
            (pl.col("bb_width") >= cfg["bb_width_min"]) &
            (pl.col("atr_pct") >= cfg["atr_pct_min"]) &
            (pl.col("atr_pct") <= cfg["atr_pct_max"]) &
            (pl.col(rsi_col) >= cfg["rsi_min"]) &
            (pl.col(rsi_col) <= cfg["rsi_max"])
        )

        if cfg["vwap_atr_max"] is not None:
            cond &= (pl.col("vwap_atr") <= cfg["vwap_atr_max"])

        if cfg["ema5_required"] == "above":
            cond &= pl.col("above_ema5")

        valid_rows = time_rows.filter(cond)

        all_pass = valid_rows.height > 0
        signal_time = valid_rows["datetime"][0] if all_pass else None

        return {
            "bb_pass": bb_pass,
            "atr_pass": atr_pass,
            "rsi_pass": rsi_pass,
            "vwap_pass": vwap_pass,
            "ema5_pass": ema5_pass,
            "all_pass": all_pass,
            "bb_max": bb_max,
            "atr_max": atr_max,
            "atr_min": atr_min,
            "rsi_last": rsi_last,
            "signal_time": signal_time,
        }

    except Exception as e:
        print(f"Error in {path}: {e}")
        return None


def run_strategy(files, trade_date, name, cfg, rsi_col):
    counts = {k: 0 for k in ["has_data","bb_pass","atr_pass","rsi_pass","vwap_pass","ema5_pass","all_pass"]}
    candidates = []

    for fname in sorted(files):
        symbol = fname.replace(".parquet", "")
        if symbol == "NIFTY50":
            continue

        result = analyse_symbol(os.path.join(ASSEMBLED_DIR, fname), trade_date, cfg, rsi_col)
        if result is None:
            continue

        counts["has_data"] += 1
        for k in counts:
            if k != "has_data" and result[k]:
                counts[k] += 1

        if result["all_pass"]:
            candidates.append((
                symbol,
                result["signal_time"],
                round(result["bb_max"],4),
                round(result["atr_max"],2),
                round(result["rsi_last"],1)
            ))

    n = max(counts["has_data"], 1)
    pct = lambda v: f"{v/n*100:.1f}%"

    print(f"\n{'─'*65}")
    print(f"  {name.upper()}")
    print(f"{'─'*65}")
    print(f"  Symbols with data    : {counts['has_data']:5d}    100.0%")
    print(f"  BB pass              : {counts['bb_pass']:5d}    {pct(counts['bb_pass'])}")
    print(f"  ATR pass             : {counts['atr_pass']:5d}    {pct(counts['atr_pass'])}")
    print(f"  RSI pass             : {counts['rsi_pass']:5d}    {pct(counts['rsi_pass'])}")
    print(f"  ALL filters passed   : {counts['all_pass']:5d}    {pct(counts['all_pass'])}")
    print(f"{'─'*65}")

    if candidates:
        print(f"\n  ✅ CANDIDATES:")
        print(f"  {'Symbol':<12} {'Time':<20} {'BB':>6} {'ATR':>6} {'RSI':>6}")
        print(f"  {'─'*55}")
        for sym, t, bb, atr, rsi in candidates:
            print(f"  {sym:<12} {str(t):<20} {bb:>6} {atr:>6} {rsi:>6}")
    else:
        print("\n  ⚪ No candidates")


def run(trade_date: date):
    print(f"\n{'='*65}")
    print(f"  FILTER FUNNEL REPORT — {trade_date}")
    print(f"{'='*65}")

    files = [f for f in os.listdir(ASSEMBLED_DIR) if f.endswith(".parquet")]

    run_strategy(files, trade_date, "mean_reversion_short",
                 STRATEGIES["mean_reversion_short"], "rsi_simple")

    run_strategy(files, trade_date, "mb_crossover_long",
                 STRATEGIES["mb_crossover_long"], "rsi_wilder")

    print(f"\n{'='*65}\n")


if __name__ == "__main__":
    trade_date = date.today() if len(sys.argv) == 1 else date.fromisoformat(sys.argv[1])
    run(trade_date)
