#!/usr/bin/env python3
"""
Analyze why mean_reversion_short didn't generate signals today.
Shows how many symbols pass each filter condition.

Usage: python3 analyze_signals.py [YYYY-MM-DD]
"""

import sys
import polars as pl
from datetime import datetime, date
import os

# Strategy parameters (copy from mean_reversion_short.py)
BB_PERIOD = 20
BB_STD = 2.0
RSI_PERIOD = 14
ATR_PERIOD = 14
RVOL_PERIOD = 20

def analyze_day(trade_date: date):
    """Analyze all symbols for a given date."""
    
    assembled_dir = "assembled_candles"
    
    if not os.path.isdir(assembled_dir):
        print(f"❌ Directory not found: {assembled_dir}")
        return
    
    files = [f for f in os.listdir(assembled_dir) if f.endswith(".parquet")]
    
    if not files:
        print("❌ No assembled parquet files found")
        return
    
    print(f"📅 Analyzing {trade_date} with {len(files)} symbols")
    print("=" * 70)
    
    # Statistics
    stats = {
        "total_symbols": len(files),
        "has_data": 0,
        "time_filter": 0,
        "c1_trigger": 0,
        "c2_red": 0,
        "c2_below_ub": 0,
        "vwap_atr": 0,
        "bb_width": 0,
        "atr_pct": 0,
        "rsi": 0,
        "ema_dist": 0,
        "all_conditions": 0,
    }
    
    candidates = []
    
    for fname in files:
        symbol = fname.replace(".parquet", "")
        path = os.path.join(assembled_dir, fname)
        
        try:
            df = pl.read_parquet(path).sort("datetime")
            
            # Filter to trade date
            trade_date_start = datetime.combine(trade_date, datetime.min.time())
            df = df.filter(pl.col("datetime").dt.date() == trade_date)
            
            if df.is_empty():
                continue
            
            stats["has_data"] += 1
            
            # Run through strategy logic
            close = pl.col("close")
            lf = df.lazy()
            
            # Bollinger Bands
            lf = lf.with_columns([
                close.rolling_mean(BB_PERIOD).alias("mb"),
                close.rolling_std(BB_PERIOD).alias("std"),
            ]).with_columns([
                (pl.col("mb") + BB_STD * pl.col("std")).alias("ub"),
                (pl.col("mb") - BB_STD * pl.col("std")).alias("lb"),
            ]).with_columns(
                ((pl.col("ub") - pl.col("lb")) / pl.col("mb")).alias("bb_width")
            )
            
            # RSI
            lf = lf.with_columns((close - close.shift(1)).alias("delta"))
            lf = lf.with_columns([
                pl.when(pl.col("delta") > 0).then(pl.col("delta")).otherwise(0)
                  .rolling_mean(RSI_PERIOD).alias("avg_gain"),
                pl.when(pl.col("delta") < 0).then(-pl.col("delta")).otherwise(0)
                  .rolling_mean(RSI_PERIOD).alias("avg_loss"),
            ])
            lf = lf.with_columns(
                (100 - (100 / (1 + pl.col("avg_gain") / pl.col("avg_loss")))).alias("rsi")
            )
            
            # VWAP
            lf = lf.with_columns(pl.col("datetime").dt.date().alias("trade_date"))
            lf = lf.with_columns(
                ((pl.col("close") * pl.col("volume")).cum_sum().over("trade_date") /
                 pl.col("volume").cum_sum().over("trade_date")).alias("vwap")
            )
            
            # ATR
            lf = lf.with_columns([
                pl.max_horizontal(
                    pl.col("high") - pl.col("low"),
                    (pl.col("high") - close.shift()).abs(),
                    (pl.col("low") - close.shift()).abs(),
                ).rolling_mean(ATR_PERIOD).alias("atr")
            ])
            lf = lf.with_columns([
                ((pl.col("close") - pl.col("vwap")) / pl.col("atr")).alias("vwap_atr"),
                (pl.col("atr") / close * 100).alias("atr_pct"),
            ])
            
            # RVOL
            lf = lf.with_columns([
                pl.col("volume").cast(pl.Float64).alias("volume_f"),
                pl.col("volume").cast(pl.Float64).rolling_mean(RVOL_PERIOD).alias("vol_sma"),
            ])
            lf = lf.with_columns(
                pl.when(pl.col("vol_sma") > 0).then(pl.col("volume_f") / pl.col("vol_sma"))
                  .otherwise(None).alias("rvol")
            )
            
            lf = lf.with_columns(
                ((pl.col("close") - pl.col("lb")) / (pl.col("ub") - pl.col("lb")))
                .alias("close_band_pos")
            )
            
            # C1 candle
            lf = lf.with_columns([
                close.shift(1).alias("c1_close"),
                pl.col("ub").shift(1).alias("ub_prev"),
                pl.col("high").shift(1).alias("c1_high"),
                pl.col("close_band_pos").shift(1).alias("c1_close_band_pos"),
            ])
            
            # EMA 5
            lf = lf.with_columns(close.ewm_mean(span=5, adjust=False).alias("ema_5"))
            lf = lf.with_columns((close < pl.col("ema_5")).alias("c2_below_ema5"))
            lf = lf.with_columns(
                ((pl.col("close") - pl.col("ema_5")) / pl.col("atr")).alias("ema5_dist_atr")
            )
            
            # Time filter
            lf_time = lf.filter(
                ((pl.col("datetime").dt.hour() == 9) & (pl.col("datetime").dt.minute() >= 30)) |
                ((pl.col("datetime").dt.hour() == 10) & (pl.col("datetime").dt.minute() <= 45)) |
                (pl.col("datetime").dt.hour() == 14) |
                ((pl.col("datetime").dt.hour() == 15) & (pl.col("datetime").dt.minute() <= 5))
            )
            
            if lf_time.count().collect()[0, 0] > 0:
                stats["time_filter"] += 1
                
                # Individual conditions
                df_collected = lf_time.collect()
                
                if not df_collected.is_empty():
                    # C1 trigger
                    c1_pass = df_collected.filter(pl.col("c1_high") > pl.col("ub_prev"))
                    if not c1_pass.is_empty():
                        stats["c1_trigger"] += 1
                        
                        # C2 conditions
                        c2_red = c1_pass.filter(pl.col("close") < pl.col("open"))
                        if not c2_red.is_empty():
                            stats["c2_red"] += 1
                            
                            c2_below = c2_red.filter(pl.col("close") < pl.col("ub_prev"))
                            if not c2_below.is_empty():
                                stats["c2_below_ub"] += 1
                                
                                # Parameter filters
                                vwap_ok = c2_below.filter(pl.col("vwap_atr") <= 1.25)
                                if not vwap_ok.is_empty():
                                    stats["vwap_atr"] += 1
                                    
                                    bb_ok = vwap_ok.filter(pl.col("bb_width") >= 0.075)
                                    if not bb_ok.is_empty():
                                        stats["bb_width"] += 1
                                        
                                        atr_ok = bb_ok.filter(
                                            (pl.col("atr_pct") >= 1.0) & (pl.col("atr_pct") <= 2.0)
                                        )
                                        if not atr_ok.is_empty():
                                            stats["atr_pct"] += 1
                                            
                                            rsi_ok = atr_ok.filter(
                                                (pl.col("rsi") >= 55) & (pl.col("rsi") < 80)
                                            )
                                            if not rsi_ok.is_empty():
                                                stats["rsi"] += 1
                                                
                                                ema_ok = rsi_ok.filter(
                                                    (pl.col("ema5_dist_atr") <= 0.7) &
                                                    ((pl.col("ema5_dist_atr") < 0) | (pl.col("ema5_dist_atr") >= 0.15))
                                                )
                                                if not ema_ok.is_empty():
                                                    stats["ema_dist"] += 1
                                                    stats["all_conditions"] += 1
                                                    candidates.append(symbol)
        
        except Exception as e:
            print(f"⚠️ Error processing {symbol}: {e}")
            continue
    
    # Print results
    print("\n📊 FILTER FUNNEL:")
    print(f"Total symbols:              {stats['total_symbols']}")
    print(f"Have data for date:         {stats['has_data']}")
    print(f"Pass time filter:           {stats['time_filter']}")
    print(f"C1 breaks above UB:         {stats['c1_trigger']}")
    print(f"C2 red candle:              {stats['c2_red']}")
    print(f"C2 closes below UB:         {stats['c2_below_ub']}")
    print(f"VWAP_ATR <= 1.25:           {stats['vwap_atr']}")
    print(f"BB_width >= 0.075:          {stats['bb_width']}")
    print(f"ATR% 1.0-2.0:               {stats['atr_pct']}")
    print(f"RSI 55-80:                  {stats['rsi']}")
    print(f"EMA distance OK:            {stats['ema_dist']}")
    print(f"✅ All conditions:          {stats['all_conditions']}")
    
    if candidates:
        print(f"\n🎯 CANDIDATES: {', '.join(candidates)}")
    else:
        print("\n❌ NO CANDIDATES - Tightest filter:")
        # Find biggest drop-off
        filters = [
            ("Time window", stats['has_data'], stats['time_filter']),
            ("C1 trigger", stats['time_filter'], stats['c1_trigger']),
            ("C2 red candle", stats['c1_trigger'], stats['c2_red']),
            ("C2 below UB", stats['c2_red'], stats['c2_below_ub']),
            ("VWAP_ATR", stats['c2_below_ub'], stats['vwap_atr']),
            ("BB_width", stats['vwap_atr'], stats['bb_width']),
            ("ATR%", stats['bb_width'], stats['atr_pct']),
            ("RSI", stats['atr_pct'], stats['rsi']),
            ("EMA distance", stats['rsi'], stats['ema_dist']),
        ]
        
        max_drop = max(filters, key=lambda x: x[1] - x[2] if x[1] > 0 else 0)
        print(f"   {max_drop[0]}: {max_drop[1]} → {max_drop[2]} ({max_drop[1] - max_drop[2]} eliminated)")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        trade_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    else:
        trade_date = date.today()
    
    analyze_day(trade_date)
