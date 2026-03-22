"""
nifty_options_recorder.py
─────────────────────────
Records Nifty options OHLCV + OI + IV + Greeks every 5 minutes
during market hours (Mon–Fri 09:15–15:30 IST).

Output: ~/trading/Bollinger/orb_strategy/data/options/nifty_options_YYYY-MM-DD.csv

One row = one strike × one completed 5-min candle.

Reads from:
  - $KITE_API_KEY / $KITE_ACCESS_TOKEN  (already in .env.kite)
  - ~/trading/Bollinger/orb_strategy/data/nifty_expiry_calendar.csv

Run via start_all.sh (tmux session: options_recorder)
"""

import csv
import logging
import math
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import schedule
from kiteconnect import KiteConnect
from scipy.optimize import brentq
from scipy.stats import norm

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR     = Path.home() / "trading/Bollinger"
DATA_DIR     = BASE_DIR / "orb_strategy/data/options"
CALENDAR_CSV = BASE_DIR / "orb_strategy/data/nifty_expiry_calendar.csv"
LOG_DIR      = BASE_DIR / "logs"

# ── Market hours ──────────────────────────────────────────────────────────────
MARKET_OPEN  = datetime.strptime("09:15", "%H:%M").time()
MARKET_CLOSE = datetime.strptime("15:30", "%H:%M").time()

# ── Strike filter: ATM ± N% ───────────────────────────────────────────────────
STRIKE_RANGE_PCT = float(os.environ.get("STRIKE_RANGE_PCT", "10"))

# ── Black-Scholes risk-free rate ──────────────────────────────────────────────
RISK_FREE_RATE = 0.065

# ── CSV columns ───────────────────────────────────────────────────────────────
COLUMNS = [
    "timestamp", "expiry", "strike", "underlying_close",
    "ce_open", "ce_high", "ce_low", "ce_close", "ce_volume",
    "pe_open", "pe_high", "pe_low", "pe_close", "pe_volume",
    "ce_oi", "ce_oi_change", "pe_oi", "pe_oi_change",
    "ce_iv", "ce_delta", "ce_gamma", "ce_theta", "ce_vega",
    "pe_iv", "pe_delta", "pe_gamma", "pe_theta", "pe_vega",
]


# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "options_recorder.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Kite client
# ─────────────────────────────────────────────────────────────────────────────

def get_kite() -> KiteConnect:
    api_key      = os.environ["KITE_API_KEY"]
    access_token = os.environ["KITE_ACCESS_TOKEN"]
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


# ─────────────────────────────────────────────────────────────────────────────
# Expiry — read from existing calendar CSV (already refreshed by start_all.sh)
# ─────────────────────────────────────────────────────────────────────────────

def get_upcoming_expiry() -> str:
    """Return the nearest upcoming expiry date string (YYYY-MM-DD)."""
    df = pd.read_csv(CALENDAR_CSV, parse_dates=["expiry"])
    today = pd.Timestamp(date.today())
    upcoming = df[df["expiry"] >= today].sort_values("expiry")
    if upcoming.empty:
        raise RuntimeError(
            f"No upcoming expiry found in {CALENDAR_CSV}. "
            "Run download_nfo_instruments.py to refresh."
        )
    expiry = upcoming.iloc[0]["expiry"].date().isoformat()
    log.info(f"Using expiry: {expiry}")
    return expiry


# ─────────────────────────────────────────────────────────────────────────────
# Instruments — fetched once per day, filtered to expiry + strike range
# ─────────────────────────────────────────────────────────────────────────────

# Session cache — refreshed once per calendar day
_cache: dict = {
    "date":        None,
    "expiry":      None,
    "instruments": {},   # {strike: {"CE": inst, "PE": inst}}
    "prev_oi":     {},   # {tradingsymbol: last_oi}
}


def refresh_cache(kite: KiteConnect, spot: float):
    """Rebuild instrument cache if it's a new day or first run."""
    today = date.today().isoformat()
    if _cache["date"] == today:
        return

    expiry = get_upcoming_expiry()
    log.info("Fetching NFO instruments from Kite...")
    all_instruments = kite.instruments("NFO")

    lo = spot * (1 - STRIKE_RANGE_PCT / 100)
    hi = spot * (1 + STRIKE_RANGE_PCT / 100)

    strikes: dict = {}
    for inst in all_instruments:
        if (
            inst["name"] == "NIFTY"
            and str(inst["expiry"]) == expiry
            and inst["instrument_type"] in ("CE", "PE")
            and lo <= inst["strike"] <= hi
        ):
            s = int(inst["strike"])
            strikes.setdefault(s, {})
            strikes[s][inst["instrument_type"]] = inst

    _cache.update(date=today, expiry=expiry, instruments=strikes, prev_oi={})
    log.info(
        f"Cache ready — expiry: {expiry}, "
        f"strikes: {len(strikes)} (spot={spot:.0f}, ±{STRIKE_RANGE_PCT}%)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Black-Scholes helpers
# ─────────────────────────────────────────────────────────────────────────────

def _bs_price(S, K, T, r, sigma, kind):
    if T <= 0 or sigma <= 0:
        return max(S - K, 0) if kind == "CE" else max(K - S, 0)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if kind == "CE":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def calc_iv(ltp, S, K, T, kind) -> float | None:
    if T <= 0 or ltp <= 0:
        return None
    intrinsic = max(S - K, 0) if kind == "CE" else max(K - S, 0)
    if ltp < intrinsic:
        return None
    try:
        return brentq(
            lambda s: _bs_price(S, K, T, RISK_FREE_RATE, s, kind) - ltp,
            1e-6, 10.0, xtol=1e-6, maxiter=200,
        )
    except (ValueError, RuntimeError):
        return None


def calc_greeks(S, K, T, sigma, kind) -> dict:
    empty = dict(delta=None, gamma=None, theta=None, vega=None)
    if not sigma or T <= 0:
        return empty
    d1 = (math.log(S / K) + (RISK_FREE_RATE + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    pdf_d1 = norm.pdf(d1)
    sqrt_T  = math.sqrt(T)
    gamma   = round(pdf_d1 / (S * sigma * sqrt_T), 6)
    vega    = round(S * pdf_d1 * sqrt_T / 100, 6)
    if kind == "CE":
        delta = round(norm.cdf(d1), 6)
        theta = round((-S * pdf_d1 * sigma / (2 * sqrt_T)
                       - RISK_FREE_RATE * K * math.exp(-RISK_FREE_RATE * T) * norm.cdf(d2)) / 365, 6)
    else:
        delta = round(norm.cdf(d1) - 1, 6)
        theta = round((-S * pdf_d1 * sigma / (2 * sqrt_T)
                       + RISK_FREE_RATE * K * math.exp(-RISK_FREE_RATE * T) * norm.cdf(-d2)) / 365, 6)
    return dict(delta=delta, gamma=gamma, theta=theta, vega=vega)


def time_to_expiry(expiry_str: str) -> float:
    exp = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    days = (exp - date.today()).days
    return max(days / 365.0, 1 / 365.0)


# ─────────────────────────────────────────────────────────────────────────────
# Data fetchers
# ─────────────────────────────────────────────────────────────────────────────

def fetch_spot(kite: KiteConnect) -> float:
    return kite.quote("NSE:NIFTY 50")["NSE:NIFTY 50"]["last_price"]


def fetch_last_candle(kite: KiteConnect, token: int) -> dict | None:
    """Fetch the last completed 5-min candle for an instrument token."""
    now     = datetime.now()
    from_dt = now - timedelta(minutes=20)
    try:
        candles = kite.historical_data(
            token,
            from_date=from_dt,
            to_date=now,
            interval="5minute",
            continuous=False,
            oi=True,
        )
    except Exception as e:
        log.warning(f"historical_data failed (token={token}): {e}")
        return None
    if not candles:
        return None
    # Use second-to-last to avoid the still-forming bar
    return candles[-2] if len(candles) >= 2 else candles[-1]


def fetch_oi_batch(kite: KiteConnect, symbols: list[str]) -> dict[str, int]:
    """Return {tradingsymbol: oi} for a list of NFO symbols."""
    result = {}
    nfo_syms = [f"NFO:{s}" for s in symbols]
    for i in range(0, len(nfo_syms), 200):
        try:
            quotes = kite.quote(nfo_syms[i:i + 200])
            for key, q in quotes.items():
                result[key.replace("NFO:", "")] = q.get("oi", 0)
        except Exception as e:
            log.warning(f"OI batch failed: {e}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# CSV writer
# ─────────────────────────────────────────────────────────────────────────────

def save_rows(rows: list[dict]):
    if not rows:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DATA_DIR / f"nifty_options_{date.today().isoformat()}.csv"
    is_new   = not filepath.exists()
    with open(filepath, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if is_new:
            writer.writeheader()
        writer.writerows(rows)
    log.info(f"Wrote {len(rows)} rows → {filepath.name}")


# ─────────────────────────────────────────────────────────────────────────────
# Main snapshot job
# ─────────────────────────────────────────────────────────────────────────────

def record_snapshot():
    now = datetime.now()
    if now.weekday() >= 5 or not (MARKET_OPEN <= now.time() <= MARKET_CLOSE):
        log.debug("Outside market hours — skipped.")
        return

    try:
        kite = get_kite()
        spot = fetch_spot(kite)

        refresh_cache(kite, spot)

        expiry   = _cache["expiry"]
        strikes  = _cache["instruments"]
        T        = time_to_expiry(expiry)
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        if not strikes:
            log.warning("No strikes in cache — skipping.")
            return

        # OI snapshot for all symbols in one batch
        all_syms = [
            inst["tradingsymbol"]
            for d in strikes.values()
            for inst in d.values()
        ]
        oi_now = fetch_oi_batch(kite, all_syms)

        rows = []
        for strike in sorted(strikes.keys()):
            row: dict = {
                "timestamp":        timestamp,
                "expiry":           expiry,
                "strike":           strike,
                "underlying_close": round(spot, 2),
            }

            for side in ("CE", "PE"):
                pfx  = side.lower()
                inst = strikes[strike].get(side)

                if inst is None:
                    for col in ["open","high","low","close","volume",
                                "oi","oi_change","iv","delta","gamma","theta","vega"]:
                        row[f"{pfx}_{col}"] = None
                    continue

                # OHLCV
                candle = fetch_last_candle(kite, inst["instrument_token"])
                ltp = 0.0
                if candle:
                    row[f"{pfx}_open"]   = candle.get("open")
                    row[f"{pfx}_high"]   = candle.get("high")
                    row[f"{pfx}_low"]    = candle.get("low")
                    row[f"{pfx}_close"]  = candle.get("close")
                    row[f"{pfx}_volume"] = candle.get("volume")
                    ltp = candle.get("close") or 0.0
                else:
                    for col in ["open","high","low","close","volume"]:
                        row[f"{pfx}_{col}"] = None

                # OI + change
                sym         = inst["tradingsymbol"]
                oi_cur      = oi_now.get(sym, 0)
                oi_prev     = _cache["prev_oi"].get(sym, oi_cur)
                row[f"{pfx}_oi"]        = oi_cur
                row[f"{pfx}_oi_change"] = oi_cur - oi_prev

                # IV + Greeks
                iv = calc_iv(ltp, spot, strike, T, side) if ltp > 0 else None
                row[f"{pfx}_iv"] = round(iv * 100, 4) if iv else None
                g = calc_greeks(spot, strike, T, iv or 0, side)
                row[f"{pfx}_delta"] = g["delta"]
                row[f"{pfx}_gamma"] = g["gamma"]
                row[f"{pfx}_theta"] = g["theta"]
                row[f"{pfx}_vega"]  = g["vega"]

            rows.append(row)

        _cache["prev_oi"].update(oi_now)
        save_rows(rows)
        log.info(f"Snapshot done — {len(rows)} strikes | spot={spot:.2f} | expiry={expiry}")

    except Exception as e:
        log.error(f"Snapshot failed: {e}", exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("═" * 60)
    log.info("Nifty Options Recorder — OHLCV + OI + IV + Greeks")
    log.info(f"  Strike range : ±{STRIKE_RANGE_PCT}%")
    log.info(f"  Market hours : {MARKET_OPEN}–{MARKET_CLOSE} IST")
    log.info(f"  Output dir   : {DATA_DIR}")
    log.info(f"  Expiry cal   : {CALENDAR_CSV}")
    log.info("═" * 60)

    record_snapshot()
    schedule.every(5).minutes.do(record_snapshot)

    while True:
        schedule.run_pending()
        time.sleep(15)
