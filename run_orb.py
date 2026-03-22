# orb_strategy/run_orb.py
#
# ORB Options Paper/Live Executor
#
# Reads NIFTY50.parquet from assembled_candles pipeline.
# Uses REAL Kite option prices instead of Black-Scholes.
# Greeks dropped — entry/exit decisions are spot-based only.
#
# Usage:
#   cd /home/shona_nagpal/trading/Bollinger
#   python3 -m orb_strategy.run_orb [paper|live]

from __future__ import annotations

import sys
import uuid
import time
import logging
import os
from datetime import datetime, date, timedelta
import numpy as np
import pandas as pd
import polars as pl
import pytz

sys.path.insert(0, "/home/shona_nagpal/trading/Bollinger")

import orb_strategy.config as C
from orb_strategy.orb_db import (
    create_tables,
    insert_orb_trade,
    close_orb_trade,
    get_open_orb_trade,
    upsert_orb_state,
    get_orb_state,
)

IST = pytz.timezone("Asia/Kolkata")

# ══════════════════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════════════════

os.makedirs(C.LOG_DIR, exist_ok=True)

logger = logging.getLogger("orb")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(C.LOG_FILE)
    sh = logging.StreamHandler(sys.stdout)
    fh.setLevel(logging.DEBUG)
    sh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    logger.propagate = False
log = logger


# ══════════════════════════════════════════════════════════════════════════════
#  KITE CONNECTION
# ══════════════════════════════════════════════════════════════════════════════

_kite_instance = None

def _get_kite():
    global _kite_instance
    if _kite_instance is not None:
        return _kite_instance
    try:
        from kiteconnect import KiteConnect
        api_key      = os.environ.get("KITE_API_KEY", "")
        access_token = os.environ.get("KITE_ACCESS_TOKEN", "")
        if not api_key or not access_token:
            log.warning("[Kite] KITE_API_KEY / KITE_ACCESS_TOKEN not set")
            return None
        k = KiteConnect(api_key=api_key)
        k.set_access_token(access_token)
        _kite_instance = k
        log.info("[Kite] Connection initialised")
        return k
    except Exception as e:
        log.warning(f"[Kite] Init failed: {e}", exc_info=True)
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  OPTION INSTRUMENT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def next_thursday(d: date) -> date:
    dt = datetime.combine(d, datetime.min.time())
    # If today is Thursday and market is closed, roll to next week
    if dt.weekday() == 3:
        t = now_ist().time()
        if t > C.EXIT_TIME:
            dt += timedelta(days=1)
    while dt.weekday() != 3:
        dt += timedelta(days=1)
    return dt.date()


def atm_strike(spot: float) -> int:
    return int(round(spot / C.STRIKE_STEP) * C.STRIKE_STEP)


def build_symbol(expiry: date, strike: int, opt_type: str) -> str:
    """
    Build Kite NFO tradingsymbol for NIFTY weekly options.
    Format: NIFTY{YY}{MM}{DD}{strike}{CE/PE}
    Example: NIFTY2632423800CE for 2026-03-24 strike 23800 CE
    """
    return f"NFO:NIFTY{expiry.strftime('%y%m%d')}{strike}{opt_type}"


def get_option_ltp(expiry: date, strike: int, opt_type: str) -> float | None:
    """
    Fetch real option LTP from Kite.
    Returns None if unavailable — caller falls back to None handling.
    """
    try:
        kite   = _get_kite()
        if kite is None:
            return None
        symbol = build_symbol(expiry, strike, opt_type)
        data   = kite.ltp([symbol])
        if data and symbol in data:
            return float(data[symbol]["last_price"])
        log.warning(f"[Option] LTP not found for {symbol}")
        return None
    except Exception as e:
        log.warning(f"[Option] LTP fetch failed for {strike}{opt_type}: {e}", exc_info=True)
        return None


def get_nifty_ltp() -> float | None:
    """Fetch live NIFTY 50 spot price from Kite."""
    try:
        kite = _get_kite()
        if kite is None:
            return None
        data = kite.ltp(["NSE:NIFTY 50"])
        key  = "NSE:NIFTY 50"
        if data and key in data:
            return float(data[key]["last_price"])
        return None
    except Exception as e:
        log.warning(f"[Kite] NIFTY LTP fetch failed: {e}", exc_info=True)
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def ist_time():
    return now_ist().time()


def compute_atr(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]),
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=C.ATR_PERIOD, adjust=False).mean().iloc[-1]
    return float(atr / df["close"].iloc[-1] * 100)


def load_nifty_candles() -> pd.DataFrame:
    try:
        df = pl.read_parquet(C.NIFTY_PARQUET).to_pandas()
        df.columns = [c.lower() for c in df.columns]
        if "date" in df.columns:
            df = df.rename(columns={"date": "datetime"})
        df["datetime"] = pd.to_datetime(df["datetime"])
        if df["datetime"].dt.tz is not None:
            df["datetime"] = df["datetime"].dt.tz_localize(None)
        df = df.set_index("datetime").sort_index()
        return df
    except Exception as e:
        log.error(f"Failed to load NIFTY50.parquet: {e}", exc_info=True)
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
#  ORB SIGNAL ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class ORBSignal:
    def __init__(self):
        self.today         = None
        self.orb_high      = None
        self.orb_low       = None
        self.orb_range_pct = None
        self.orb_formed    = False
        self.signal_fired  = False
        self.regime        = None
        self.prior_range   = None
        log.info("[ORBSignal] Initialised.")

    def _reset_day(self, today: date, df: pd.DataFrame):
        try:
            self.today         = today
            self.orb_high      = None
            self.orb_low       = None
            self.orb_range_pct = None
            self.orb_formed    = False
            self.signal_fired  = False

            # Regime: daily close vs 20D EMA
            daily_close = df["close"].resample("1D").last().dropna()
            daily_close.index = daily_close.index.normalize()
            if len(daily_close) >= C.REGIME_EMA_PERIOD:
                ema         = daily_close.ewm(span=C.REGIME_EMA_PERIOD, adjust=False).mean()
                prior       = ema[ema.index.date < today]
                prior_close = daily_close[daily_close.index.date < today]
                if not prior.empty and not prior_close.empty:
                    self.regime = "bull" if prior_close.iloc[-1] > prior.iloc[-1] else "bear"
                else:
                    self.regime = None
            else:
                self.regime = None

            # Prior day range %
            daily_high   = df["high"].resample("1D").max().dropna()
            daily_low    = df["low"].resample("1D").min().dropna()
            daily_close2 = df["close"].resample("1D").last().dropna()
            daily_high.index   = daily_high.index.normalize()
            daily_low.index    = daily_low.index.normalize()
            daily_close2.index = daily_close2.index.normalize()

            prev_high  = daily_high[daily_high.index.date < today]
            prev_low   = daily_low[daily_low.index.date < today]
            prev_close = daily_close2[daily_close2.index.date < today]

            if not prev_high.empty and not prev_low.empty and not prev_close.empty:
                self.prior_range = float(
                    (prev_high.iloc[-1] - prev_low.iloc[-1]) / prev_close.iloc[-1] * 100
                )
            else:
                self.prior_range = None

            log.info(
                f"[ORBSignal] New day {today} | regime={self.regime} "
                + (f"| prior_range={self.prior_range:.3f}%" if self.prior_range else "| prior_range=None")
            )

            # Restore from DB on restart
            state = get_orb_state(today)
            if state:
                self.orb_high      = float(state["orb_high"])      if state["orb_high"]      else None
                self.orb_low       = float(state["orb_low"])        if state["orb_low"]        else None
                self.orb_range_pct = float(state["orb_range_pct"]) if state["orb_range_pct"] else None
                self.orb_formed    = bool(state["orb_formed"])
                self.signal_fired  = bool(state["signal_fired"])
                if self.orb_formed:
                    log.info(
                        f"[ORBSignal] Restored state from DB: "
                        f"ORB {self.orb_low:.2f}–{self.orb_high:.2f} "
                        f"| signal_fired={self.signal_fired}"
                    )

        except Exception as e:
            log.error(f"[ORBSignal] _reset_day crashed for {today}: {e}", exc_info=True)
            self.orb_formed   = False
            self.signal_fired = True   # skip today safely
            self.regime       = None
            self.prior_range  = None

    def check(self, df: pd.DataFrame) -> dict | None:
        today = now_ist().date()

        if self.today != today:
            self._reset_day(today, df)

        if self.signal_fired:
            return None

        t = ist_time()
        if t < C.MARKET_OPEN or t >= C.ENTRY_CUTOFF_TIME:
            return None

        today_df = df[df.index.date == today].copy()
        if today_df.empty:
            return None

        # Form ORB
        if not self.orb_formed:
            if t < C.ORB_END_TIME:
                return None

            orb_bars = today_df[today_df.index.time <= C.ORB_END_TIME]
            if orb_bars.empty:
                return None

            self.orb_high      = float(orb_bars["high"].max())
            self.orb_low       = float(orb_bars["low"].min())
            last_close         = float(orb_bars["close"].iloc[-1])
            self.orb_range_pct = (self.orb_high - self.orb_low) / last_close * 100

            if not (C.ORB_RANGE_MIN_PCT <= self.orb_range_pct <= C.ORB_RANGE_MAX_PCT):
                log.info(f"[ORBSignal] ORB range {self.orb_range_pct:.3f}% outside filter — skip day")
                self.signal_fired = True
                return None

            if self.prior_range is None or self.prior_range >= C.PRIOR_DAY_RANGE_MAX:
                log.info(f"[ORBSignal] Prior day range {self.prior_range}% >= {C.PRIOR_DAY_RANGE_MAX}% — skip day")
                self.signal_fired = True
                return None

            if self.regime is None:
                log.info("[ORBSignal] Regime unknown — skip day")
                self.signal_fired = True
                return None

            self.orb_formed = True
            log.info(
                f"[ORBSignal] ORB formed: HIGH={self.orb_high:.2f} "
                f"LOW={self.orb_low:.2f} range={self.orb_range_pct:.3f}% "
                f"| regime={self.regime}"
            )
            upsert_orb_state({
                "trade_date":    today,
                "orb_high":      self.orb_high,
                "orb_low":       self.orb_low,
                "orb_range_pct": self.orb_range_pct,
                "orb_formed":    True,
                "signal_fired":  False,
                "direction":     None,
                "regime":        self.regime,
            })

        # Watch for breakout
        post_orb = today_df[today_df.index.time > C.ORB_END_TIME]
        if post_orb.empty:
            return None

        latest_close = float(post_orb.iloc[-1]["close"])
        direction    = None

        if latest_close > self.orb_high:
            direction = "CE"
        elif latest_close < self.orb_low:
            direction = "PE"

        if direction is None:
            return None

        if direction == "CE" and self.regime != "bull":
            log.info(f"[ORBSignal] CE signal but regime={self.regime} — skip")
            self.signal_fired = True
            return None
        if direction == "PE" and self.regime != "bear":
            log.info(f"[ORBSignal] PE signal but regime={self.regime} — skip")
            self.signal_fired = True
            return None

        atr_pct = compute_atr(today_df.tail(C.ATR_PERIOD + 5))
        if atr_pct < C.ATR_MIN_PCT:
            log.info(f"[ORBSignal] ATR {atr_pct:.4f}% < {C.ATR_MIN_PCT}% — skip")
            self.signal_fired = True
            return None

        self.signal_fired = True
        upsert_orb_state({
            "trade_date":    today,
            "orb_high":      self.orb_high,
            "orb_low":       self.orb_low,
            "orb_range_pct": self.orb_range_pct,
            "orb_formed":    True,
            "signal_fired":  True,
            "direction":     direction,
            "regime":        self.regime,
        })

        log.info(
            f"[ORBSignal] SIGNAL {direction} | spot={latest_close:.2f} "
            f"| ATR={atr_pct:.4f}% | prior_range={self.prior_range:.3f}%"
        )

        return {
            "direction":           direction,
            "regime":              self.regime,
            "spot":                latest_close,
            "orb_high":            self.orb_high,
            "orb_low":             self.orb_low,
            "orb_range_pct":       self.orb_range_pct,
            "atr_pct":             atr_pct,
            "prior_day_range_pct": self.prior_range,
            "signal_time":         now_ist(),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  ORB EXECUTOR
# ══════════════════════════════════════════════════════════════════════════════

class ORBExecutor:

    def __init__(self, execution_mode: str = "PAPER"):
        self.execution_mode = execution_mode.upper()
        self.active_trade   = None
        log.info(f"[ORBExecutor] Mode: {self.execution_mode}")

    def _option_entry(self, signal: dict) -> dict | None:
        try:
            spot      = signal["spot"]
            direction = signal["direction"]
            today     = date.today()
            expiry    = next_thursday(today)
            dte       = (expiry - today).days
            strike    = atm_strike(spot)
            entry_ts  = now_ist()

            # Real option price from Kite
            price = get_option_ltp(expiry, strike, direction)

            if not price or price <= 0:
                log.warning(
                    f"[ORBExecutor] Could not get real LTP for "
                    f"{strike}{direction} exp={expiry} — skip entry"
                )
                return None

            size_mult = C.PE_SIZE_MULT if direction == "PE" else C.CE_SIZE_MULT
            lots      = max(1, int(C.CAPITAL_PER_TRADE * size_mult / (price * C.LOT_SIZE)))
            units     = lots * C.LOT_SIZE
            cost      = price * units

            sl_spot_level = (
                round(spot * (1 - C.SL_SPOT_PCT / 100), 2) if direction == "CE"
                else round(spot * (1 + C.SL_SPOT_PCT / 100), 2)
            )

            symbol = build_symbol(expiry, strike, direction)
            log.info(
                f"[ORBExecutor] Option LTP: {symbol} = ₹{price:.2f} "
                f"| lots={lots} units={units} cost=₹{cost:,.0f}"
            )

            return {
                "trade_id":            str(uuid.uuid4()),
                "execution_mode":      self.execution_mode,
                "trade_date":          today,
                "direction":           direction,
                "regime":              signal["regime"],
                "orb_high":            signal["orb_high"],
                "orb_low":             signal["orb_low"],
                "orb_range_pct":       round(signal["orb_range_pct"], 4),
                "atr_pct_at_entry":    round(signal["atr_pct"], 4),
                "prior_day_range_pct": round(signal["prior_day_range_pct"], 4),
                "entry_time":          entry_ts,
                "strike":              strike,
                "expiry":              expiry,
                "dte_at_entry":        dte,
                "spot_entry":          round(spot, 2),
                "sl_spot_level":       sl_spot_level,
                "vix_on_entry":        None,   # not needed — using real prices
                "iv_entry_pct":        None,   # not needed — using real prices
                "premium_entry":       round(price, 2),
                "lots":                lots,
                "units":               units,
                "cost_outflow":        round(cost, 2),
                "delta_entry":         None,   # Greeks dropped
                "gamma_entry":         None,
                "theta_entry_per_day": None,
                "vega_entry_per_1pct": None,
            }

        except Exception as e:
            log.error(f"[ORBExecutor] _option_entry failed: {e}", exc_info=True)
            return None

    def enter(self, signal: dict):
        if self.active_trade:
            log.warning("[ORBExecutor] Already in trade — skipping entry")
            return

        trade = self._option_entry(signal)
        if not trade:
            return

        insert_orb_trade(trade)
        self.active_trade = trade

        log.info(
            f"[ORBExecutor] ENTER {trade['direction']} "
            f"| strike={trade['strike']} exp={trade['expiry']} "
            f"| spot={trade['spot_entry']:.2f} "
            f"| premium=₹{trade['premium_entry']:.2f} "
            f"| lots={trade['lots']} cost=₹{trade['cost_outflow']:,.0f}"
        )

    def monitor(self, df: pd.DataFrame):
        if not self.active_trade:
            return

        try:
            t     = ist_time()
            today = date.today()

            today_df = df[df.index.date == today]
            if today_df.empty:
                return

            # Use live NIFTY spot for SL check
            latest_spot = get_nifty_ltp()
            if latest_spot is None:
                # Fall back to latest candle close
                latest_spot = float(today_df["close"].iloc[-1])

            spot_entry = float(self.active_trade["spot_entry"])
            direction  = self.active_trade["direction"]
            trade_id   = self.active_trade["trade_id"]
            expiry     = self.active_trade["expiry"]
            strike     = self.active_trade["strike"]

            move_pct = (latest_spot - spot_entry) / spot_entry * 100
            if direction == "PE":
                move_pct = -move_pct

            sl_hit   = move_pct <= -C.SL_SPOT_PCT
            exit_eod = t >= C.EXIT_TIME

            if not sl_hit and not exit_eod:
                log.debug(
                    f"[ORBExecutor] {direction} | spot={latest_spot:.2f} "
                    f"move={move_pct:+.3f}% | SL@{-C.SL_SPOT_PCT:.2f}%"
                )
                return

            # Fetch real exit price from Kite
            price_exit = get_option_ltp(expiry, strike, direction)
            if not price_exit or price_exit <= 0:
                log.warning(
                    f"[ORBExecutor] Could not get exit LTP for {strike}{direction} "
                    f"— using 0 as exit price"
                )
                price_exit = 0.0

            units      = self.active_trade["units"]
            pnl        = (price_exit - self.active_trade["premium_entry"]) * units
            pnl_pct    = ((price_exit - self.active_trade["premium_entry"]) /
                          self.active_trade["premium_entry"] * 100
                          if self.active_trade["premium_entry"] > 0 else 0)
            spot_chg   = (latest_spot - spot_entry) / spot_entry * 100
            exit_reason = "SL_HIT" if sl_hit else "EOD_EXIT"

            exit_data = {
                "exit_time":    now_ist(),
                "spot_exit":    round(latest_spot, 2),
                "spot_chg_pct": round(spot_chg, 4),
                "premium_exit": round(price_exit, 2),
                "iv_exit_pct":  None,
                "pnl":          round(pnl, 2),
                "pnl_pct":      round(pnl_pct, 2),
                "sl_hit":       sl_hit,
                "exit_reason":  exit_reason,
            }
            close_orb_trade(trade_id, exit_data)

            emoji  = "🔴" if pnl < 0 else "🟢"
            reason = "SL HIT" if sl_hit else "EOD EXIT"
            log.info(
                f"[ORBExecutor] {emoji} {reason} {direction} "
                f"| spot={latest_spot:.2f} "
                f"| premium_exit=₹{price_exit:.2f} "
                f"| PnL=₹{pnl:,.0f} ({pnl_pct:+.1f}%)"
            )
            self.active_trade = None

        except Exception as e:
            log.error(f"[ORBExecutor] monitor failed: {e}", exc_info=True)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════

def main(execution_mode: str = "PAPER"):
    log.info("=" * 60)
    log.info(f"  Nifty ORB Options Executor  [{execution_mode}]")
    log.info("=" * 60)

    create_tables()

    signal_engine = ORBSignal()
    executor      = ORBExecutor(execution_mode)

    open_trade = get_open_orb_trade(execution_mode)
    if open_trade:
        executor.active_trade = dict(open_trade)
        log.info(
            f"[Main] Restored open trade from DB: "
            f"{open_trade['direction']} entered at {open_trade['entry_time']}"
        )

    log.info(
        f"[Main] Polling every {C.POLL_INTERVAL_SEC}s. "
        f"NIFTY50.parquet → {C.NIFTY_PARQUET}"
    )

    while True:
        try:
            t = ist_time()

            if t < C.MARKET_OPEN or t > C.EXIT_TIME:
                time.sleep(30)
                continue

            df = load_nifty_candles()
            if df.empty:
                log.warning("[Main] Empty candle data — retrying...")
                time.sleep(C.POLL_INTERVAL_SEC)
                continue

            executor.monitor(df)

            if not executor.active_trade:
                signal = signal_engine.check(df)
                if signal:
                    executor.enter(signal)

        except KeyboardInterrupt:
            log.info("[Main] Shutting down.")
            break
        except Exception as e:
            log.error(f"[Main] Unhandled error: {e}", exc_info=True)

        time.sleep(C.POLL_INTERVAL_SEC)


if __name__ == "__main__":
    mode = sys.argv[1].upper() if len(sys.argv) > 1 else "PAPER"
    if mode not in ("PAPER", "LIVE"):
        print("Usage: python3 -m orb_strategy.run_orb [paper|live]")
        sys.exit(1)
    main(mode)
