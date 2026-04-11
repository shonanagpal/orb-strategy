import polars as pl
from datetime import date

TRADE_DATE = date(2026, 3, 18)
PATH = "assembled_candles/OLECTRA.parquet"

BB_PERIOD = 20
BB_STD = 2.0
RSI_PERIOD = 14
ATR_PERIOD = 14


def run():
    df = (
        pl.read_parquet(PATH)
        .sort("datetime")
        .filter(pl.col("datetime").dt.date() == TRADE_DATE)
    )

    if df.is_empty():
        print("No data")
        return

    close = pl.col("close")
    lf = df.lazy()

    # --- Indicators (same as strategy) ---
    lf = lf.with_columns([
        close.rolling_mean(BB_PERIOD).alias("mb"),
        close.rolling_std(BB_PERIOD).alias("std"),
    ]).with_columns([
        (pl.col("mb") + BB_STD * pl.col("std")).alias("ub"),
        (pl.col("mb") - BB_STD * pl.col("std")).alias("lb"),
    ]).with_columns(
        ((pl.col("ub") - pl.col("lb")) / pl.col("mb")).alias("bb_width")
    )

    lf = lf.with_columns((close - close.shift(1)).alias("delta"))

    lf = lf.with_columns([
        pl.when(pl.col("delta") > 0).then(pl.col("delta")).otherwise(0).rolling_mean(RSI_PERIOD).alias("avg_gain"),
        pl.when(pl.col("delta") < 0).then(-pl.col("delta")).otherwise(0).rolling_mean(RSI_PERIOD).alias("avg_loss"),
    ])

    lf = lf.with_columns(
        (100 - (100 / (1 + pl.col("avg_gain") / pl.col("avg_loss")))).alias("rsi")
    )

    lf = lf.with_columns(
        pl.col("datetime").dt.date().alias("trade_date")
    ).with_columns(
        (
            (pl.col("close") * pl.col("volume")).cum_sum().over("trade_date") /
            pl.col("volume").cum_sum().over("trade_date")
        ).alias("vwap")
    )

    lf = lf.with_columns([
        pl.max_horizontal(
            pl.col("high") - pl.col("low"),
            (pl.col("high") - close.shift()).abs(),
            (pl.col("low")  - close.shift()).abs(),
        ).rolling_mean(ATR_PERIOD).alias("atr")
    ]).with_columns([
        ((pl.col("close") - pl.col("vwap")) / pl.col("atr")).alias("vwap_atr"),
        (pl.col("atr") / close * 100).alias("atr_pct"),
    ])

    lf = lf.with_columns(
        close.ewm_mean(span=5, adjust=False).alias("ema_5")
    ).with_columns(
        ((pl.col("close") - pl.col("ema_5")) / pl.col("atr")).alias("ema5_dist_atr")
    )

    lf = lf.with_columns([
        close.shift(1).alias("c1_close"),
        pl.col("ub").shift(1).alias("ub_prev"),
        pl.col("high").shift(1).alias("c1_high"),
        ((pl.col("close") - pl.col("lb")) / (pl.col("ub") - pl.col("lb"))).shift(1).alias("c1_band_pos"),
    ])

    df_all = lf.collect()

    print("\n===== OLECTRA DEBUG =====\n")

    for row in df_all.iter_rows(named=True):

        cond1 = row["c1_high"] and row["ub_prev"] and row["c1_high"] > row["ub_prev"]
        cond2 = row["c1_close"] and row["ub_prev"] and (
            row["c1_close"] > row["ub_prev"] or row["c1_band_pos"] >= 0.85
        )
        cond3 = row["close"] < row["open"]
        cond4 = row["ub_prev"] and row["close"] < row["ub_prev"]

        cond5 = row["bb_width"] and row["bb_width"] >= 0.05
        cond6 = row["atr_pct"] and 1.0 <= row["atr_pct"] <= 2.0
        cond7 = row["rsi"] and 55 <= row["rsi"] < 80
        cond8 = row["vwap_atr"] and row["vwap_atr"] <= 1.25
        cond9 = row["ema5_dist_atr"] and row["ema5_dist_atr"] <= 0.7
        cond10 = row["ema5_dist_atr"] and (
            row["ema5_dist_atr"] < 0 or row["ema5_dist_atr"] >= 0.15
        )

        core = cond1 and cond2 and cond3 and cond4
        final = all([cond1, cond2, cond3, cond4, cond5, cond6, cond7, cond8, cond9, cond10])

        if core:
            print(f"\n{row['datetime']}")
            print(f"core pattern: ✅")
            print(f"bb_width: {cond5}")
            print(f"atr_pct: {cond6}")
            print(f"rsi: {cond7}")
            print(f"vwap_atr: {cond8}")
            print(f"ema5_dist: {cond9}")
            print(f"ema5_logic: {cond10}")
            print(f"FINAL SIGNAL: {final}")


if __name__ == "__main__":
    run()
