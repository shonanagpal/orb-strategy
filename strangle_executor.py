# orb_strategy/strangle_executor.py
#
# ORB Short Strangle — live/paper executor.
#
# Designed to be called from run_orb.py main loop with two lines:
#
#     strangle_executor = StrangleExecutor(execution_mode)
#     # inside loop, after ORB monitor + signal check:
#     if not signal_engine.signal_fired:
#         strangle_executor.run(df)
#
# The ORB signal_fired flag is the gate — strangle only runs on days
# where ORB never fired (no breakout signal and no filter rejection).
#
# Strategy rules:
#   Entry  : 10:00 AM bar, spot inside ORB range, ATR < 0.25%, Mon/Tue/Fri only
#   Strangle: Sell CE at spot+1%, sell PE at spot-1% (nearest 50 strike)
#   Adjustment: if spot touches either strike → close both, re-sell 1% from new spot
#   Max adjustments: 2 (3 total cycles per day)
#   Exit   : 15:00 hard square-off
#   Sizing : 2 lots fixed, Rs.300 flat cost per cycle
#   Pricing: Real Kite LTP (no BS model)

from __future__ import annotations

import uuid
import logging
import os
from datetime import date, datetime, timedelta
from datetime import time as dtime

import pandas as pd
import pytz

import orb_strategy.strangle_config as SC
from orb_strategy.strangle_db import (
    insert_strangle_cycle,
    close_strangle_cycle,
    get_open_strangle_cycles,
    get_today_strangle_cycle_count,
)
from orb_strategy.circuit_breaker import CircuitBreaker
from orb_strategy.live_order_manager import sell_option, buy_option

IST = pytz.timezone("Asia/Kolkata")

# ── Logger (separate file from ORB) ──────────────────────────────────────────
os.makedirs(SC.LOG_DIR, exist_ok=True)
_slog = logging.getLogger("strangle")
if not _slog.handlers:
    _slog.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(SC.LOG_FILE)
    sh = logging.StreamHandler()
    fh.setLevel(logging.DEBUG)
    sh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    sh.setFormatter(fmt)
    _slog.addHandler(fh)
    _slog.addHandler(sh)
    _slog.propagate = False
log = _slog


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _now_ist() -> datetime:
    return datetime.now(IST).replace(tzinfo=None)


def _ist_time() -> dtime:
    return _now_ist().time()


# ── Expiry calendar (loaded once at import time) ─────────────────────────────
# Path: same data folder as backtester. Run download_nfo_instruments.py to
# generate/refresh this file. If absent, falls back to mathematical lookup.

_EXPIRY_CALENDAR: dict[date, date] = {}   # trade_date -> actual expiry date
_EXPIRY_CHANGE_DATE = date(2025, 9, 2)    # Nifty switched Thu->Tue from this date

def _load_expiry_calendar():
    import os
    import pandas as pd
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data", "nifty_expiry_calendar.csv"
    )
    if not os.path.exists(path):
        log.warning(
            f"[ExpiryCalendar] {path} not found — "            "using mathematical expiry lookup (holiday rollovers not handled)"
        )
        return
    try:
        df = pd.read_csv(path, parse_dates=["expiry"])
        df["expiry"] = df["expiry"].dt.date
        expiries = sorted(df["expiry"].tolist())
        # For each expiry, all trade dates in the preceding week map to it
        for i, exp in enumerate(expiries):
            prev_exp = expiries[i - 1] if i > 0 else date(2000, 1, 1)
            d = prev_exp + timedelta(days=1)
            while d <= exp:
                _EXPIRY_CALENDAR[d] = exp
                d += timedelta(days=1)
        log.info(
            f"[ExpiryCalendar] Loaded {len(expiries)} expiries "            f"({expiries[0]} → {expiries[-1]})"
        )
    except Exception as e:
        log.warning(f"[ExpiryCalendar] Load failed: {e} — using mathematical fallback")

_load_expiry_calendar()


def _next_expiry(d: date) -> date:
    """
    Return actual Nifty weekly expiry for the week containing trade date d.
    Uses expiry calendar CSV if available (handles holiday rollovers).
    Falls back to mathematical lookup:
      Pre  Sep 2 2025: next Thursday
      From Sep 2 2025: next Tuesday
    """
    # Calendar lookup first
    if d in _EXPIRY_CALENDAR:
        return _EXPIRY_CALENDAR[d]

    # Mathematical fallback
    target_weekday = 1 if d >= _EXPIRY_CHANGE_DATE else 3
    dt = d
    while dt.weekday() != target_weekday:
        dt += timedelta(days=1)
    return dt


def _next_thursday(d: date) -> date:
    """Legacy alias — use _next_expiry() for all new code."""
    return _next_expiry(d)


def _strangle_strikes(spot: float) -> tuple[int, int]:
    """Return (ce_strike, pe_strike) each STRIKE_OFFSET_PCT% from spot."""
    ce = int(round(spot * (1 + SC.STRIKE_OFFSET_PCT / 100) / SC.STRIKE_STEP) * SC.STRIKE_STEP)
    pe = int(round(spot * (1 - SC.STRIKE_OFFSET_PCT / 100) / SC.STRIKE_STEP) * SC.STRIKE_STEP)
    return ce, pe


def _build_symbol(expiry: date, strike: int, opt_type: str) -> str:
    """Kite NFO tradingsymbol. Format: NFO:NIFTY{YY}{MM}{DD}{strike}{CE/PE}"""
    return f"NFO:NIFTY{expiry.strftime('%y%m%d')}{strike}{opt_type}"


def _compute_atr_pct(df: pd.DataFrame) -> float:
    """14-period EWM ATR as % of last close."""
    if len(df) < 2:
        return 0.0
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]),
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=SC.ATR_PERIOD, adjust=False).mean().iloc[-1]
    return float(atr / df["close"].iloc[-1] * 100)


def _get_ltp(kite, symbol: str) -> float | None:
    """Fetch LTP from Kite. Returns None on any failure."""
    try:
        data = kite.ltp([symbol])
        if data and symbol in data:
            return float(data[symbol]["last_price"])
        log.warning(f"[LTP] Not found: {symbol}")
        return None
    except Exception as e:
        log.warning(f"[LTP] Fetch failed for {symbol}: {e}")
        return None


def _get_strangle_ltps(
    kite,
    expiry: date,
    ce_strike: int,
    pe_strike: int,
) -> tuple[float | None, float | None]:
    """Fetch CE and PE LTPs. Returns (ce_ltp, pe_ltp), either can be None."""
    ce_sym = _build_symbol(expiry, ce_strike, "CE")
    pe_sym = _build_symbol(expiry, pe_strike, "PE")
    ce_ltp = _get_ltp(kite, ce_sym)
    pe_ltp = _get_ltp(kite, pe_sym)
    return ce_ltp, pe_ltp


def _get_nifty_ltp(kite) -> float | None:
    try:
        data = kite.ltp(["NSE:NIFTY 50"])
        key  = "NSE:NIFTY 50"
        if data and key in data:
            return float(data[key]["last_price"])
        return None
    except Exception as e:
        log.warning(f"[LTP] NIFTY fetch failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  STRANGLE EXECUTOR
# ══════════════════════════════════════════════════════════════════════════════

class StrangleExecutor:
    """
    Manages the full intraday strangle lifecycle:
    entry at 10:00, adjustment on breach, EOD close at 15:00.

    Call strangle_executor.run(df) once per poll cycle from the main loop.
    Pass the same kite instance used by the ORB executor.
    """

    def __init__(self, kite, execution_mode: str = "PAPER"):
        self.kite           = kite
        self.execution_mode = execution_mode.upper()

        # Intraday state — reset each day
        self._today          = None
        self._entered        = False   # True once entry conditions checked at 10am
        self._done           = False   # True once EOD close fired or max adj reached
        self._active_cycle   = None    # dict of current open cycle
        self._adj_count      = 0       # adjustments made today
        self._cycle_count    = 0       # total cycles opened today
        self._orb_high       = None
        self._orb_low        = None

        # Circuit breaker — Rs.20,000 daily loss limit per strategy
        self._cb = CircuitBreaker("strangle_nifty", limit=20_000)

        log.info(f"[StrangleExecutor] Initialised — mode={self.execution_mode}")

    # ── Day reset ─────────────────────────────────────────────────────────────

    def _reset_if_new_day(self):
        today = _now_ist().date()
        if self._today == today:
            return
        self._today        = today
        self._entered      = False
        self._done         = False
        self._active_cycle = None
        self._adj_count    = 0
        self._cycle_count  = 0
        self._orb_high     = None
        self._orb_low      = None

        # Restore open cycle from DB on restart
        open_cycles = get_open_strangle_cycles(self.execution_mode)
        if open_cycles:
            row   = open_cycles[-1]   # most recent
            extra = row.get("extra") or {}
            if isinstance(extra, str):
                import json
                extra = json.loads(extra)
            self._active_cycle = {
                "cycle_id":   row["trade_id"],
                "ce_strike":  extra.get("ce_strike"),
                "pe_strike":  extra.get("pe_strike"),
                "expiry":     extra.get("expiry"),
                "ce_entry_px":extra.get("ce_entry_px"),
                "pe_entry_px":extra.get("pe_entry_px"),
                "spot_entry": extra.get("spot_entry"),
                "lots":       extra.get("lots", SC.LOTS),
                "units":      extra.get("lots", SC.LOTS) * SC.LOT_SIZE,
                "entry_time": row["entry_time"],
            }
            self._adj_count   = get_today_strangle_cycle_count(self.execution_mode) - 1
            self._cycle_count = get_today_strangle_cycle_count(self.execution_mode)
            self._entered     = True
            log.info(
                f"[StrangleExecutor] Restored open cycle from DB: "
                f"CE={self._active_cycle['ce_strike']} "
                f"PE={self._active_cycle['pe_strike']}"
            )

    # ── ORB containment check ─────────────────────────────────────────────────

    def _check_orb_containment(self, df: pd.DataFrame, today: date) -> bool:
        """
        Returns True if spot at 10:00 AM is inside the 9:15-9:45 ORB range.
        Also computes and caches orb_high, orb_low for the day.
        """
        try:
            today_df  = df[df.index.date == today]
            open_time = today_df.index[0]
            orb_end   = open_time + pd.Timedelta(minutes=SC.ORB_WINDOW_MIN)
            orb_bars  = today_df[today_df.index <= orb_end]
            if len(orb_bars) < 2:
                return False

            self._orb_high = float(orb_bars["high"].max())
            self._orb_low  = float(orb_bars["low"].min())

            # 10:00 AM bar
            entry_bars = today_df[today_df.index.time == SC.STRANGLE_ENTRY_TIME]
            if entry_bars.empty:
                return False
            spot_10am = float(entry_bars.iloc[0]["close"])

            contained = self._orb_low < spot_10am < self._orb_high
            log.info(
                f"[StrangleExecutor] ORB {self._orb_low:.0f}–{self._orb_high:.0f} "
                f"| spot_10am={spot_10am:.0f} | contained={contained}"
            )
            return contained
        except Exception as e:
            log.error(f"[StrangleExecutor] ORB containment check failed: {e}", exc_info=True)
            return False

    # ── Open a new strangle cycle ─────────────────────────────────────────────

    def _open_cycle(
        self,
        spot: float,
        expiry: date,
        atr_pct: float,
        vix_val: float | None,
    ) -> bool:
        """Fetch real LTPs, record cycle in DB. Returns True if successful."""
        ce_strike, pe_strike = _strangle_strikes(spot)
        ce_ltp, pe_ltp       = _get_strangle_ltps(
            self.kite, expiry, ce_strike, pe_strike
        )

        if ce_ltp is None or pe_ltp is None or ce_ltp <= 0 or pe_ltp <= 0:
            log.warning(
                f"[StrangleExecutor] LTP unavailable — "
                f"CE{ce_strike}={ce_ltp} PE{pe_strike}={pe_ltp} — skip"
            )
            return False

        self._cycle_count += 1
        cycle_id = str(uuid.uuid4())
        units    = SC.LOTS * SC.LOT_SIZE
        now      = _now_ist()

        cycle = {
            "cycle_id":      cycle_id,
            "cycle_num":     self._cycle_count,
            "execution_mode":self.execution_mode,
            "ce_strike":     ce_strike,
            "pe_strike":     pe_strike,
            "expiry":        expiry,
            "ce_entry_px":   round(ce_ltp, 2),
            "pe_entry_px":   round(pe_ltp, 2),
            "spot_entry":    round(spot, 2),
            "lots":          SC.LOTS,
            "units":         units,
            "entry_time":    now,
            "vix_at_entry":  vix_val,
            "atr_pct":       round(atr_pct, 4),
            "adj_count":     self._adj_count,
        }

        # LIVE: place actual sell orders on exchange
        if self.execution_mode == 'LIVE':
            units = SC.LOTS * SC.LOT_SIZE
            ce_fill = sell_option(self.kite, expiry, ce_strike, 'CE', units)
            pe_fill = sell_option(self.kite, expiry, pe_strike, 'PE', units)
            if not ce_fill or not pe_fill:
                log.error(
                    '[StrangleExecutor] LIVE entry failed — '
                    'one or both legs could not be filled — aborting cycle'
                )
                self._cycle_count -= 1
                return False
            cycle['ce_entry_px'] = round(ce_fill['avg_price'], 2)
            cycle['pe_entry_px'] = round(pe_fill['avg_price'], 2)

        insert_strangle_cycle(cycle)
        self._active_cycle = cycle
        self._entered      = True

        log.info(
            f"[StrangleExecutor] OPEN cycle={self._cycle_count} "
            f"| CE{ce_strike}@{ce_ltp:.2f}  PE{pe_strike}@{pe_ltp:.2f} "
            f"| spot={spot:.0f} lots={SC.LOTS} units={units} "
            f"| adj_remaining={SC.MAX_ADJUSTMENTS - self._adj_count}"
        )
        return True

    # ── Close current cycle ───────────────────────────────────────────────────

    def _close_cycle(self, spot: float, reason: str):
        """Fetch exit LTPs, compute PnL, write to DB."""
        if not self._active_cycle:
            return

        c        = self._active_cycle
        expiry   = c["expiry"]
        # expiry may be string if restored from DB
        if isinstance(expiry, str):
            from datetime import date as _date
            expiry = _date.fromisoformat(expiry)

        ce_ltp, pe_ltp = _get_strangle_ltps(
            self.kite, expiry, c["ce_strike"], c["pe_strike"]
        )

        # If LTP unavailable on close, use last known entry price
        # (conservative: assume no change rather than recording fake 0)
        if ce_ltp is None or ce_ltp <= 0:
            log.warning(f"[StrangleExecutor] CE exit LTP unavailable — using entry price")
            ce_ltp = c["ce_entry_px"]
        if pe_ltp is None or pe_ltp <= 0:
            log.warning(f"[StrangleExecutor] PE exit LTP unavailable — using entry price")
            pe_ltp = c["pe_entry_px"]

        units     = c["units"]
        gross_pnl = ((c["ce_entry_px"] - ce_ltp) +
                     (c["pe_entry_px"] - pe_ltp)) * units
        cost      = SC.COST_PER_CYCLE
        net_pnl   = gross_pnl - cost

        exit_data = {
            "ce_exit_px":       round(ce_ltp, 2),
            "pe_exit_px":       round(pe_ltp, 2),
            "spot_exit":        round(spot, 2),
            "exit_time":        _now_ist(),
            "exit_reason":      reason,
            "gross_pnl":        round(gross_pnl, 2),
            "transaction_cost": cost,
            "net_pnl":          round(net_pnl, 2),
        }

        # LIVE: place actual buy orders to close positions
        if self.execution_mode == 'LIVE':
            units = c['units']
            ce_close = buy_option(self.kite, expiry, c['ce_strike'], 'CE', units)
            pe_close = buy_option(self.kite, expiry, c['pe_strike'], 'PE', units)
            if ce_close:
                exit_data['ce_exit_px'] = round(ce_close['avg_price'], 2)
            if pe_close:
                exit_data['pe_exit_px'] = round(pe_close['avg_price'], 2)
            gross_pnl = ((c['ce_entry_px'] - exit_data['ce_exit_px']) +
                         (c['pe_entry_px'] - exit_data['pe_exit_px'])) * c['units']
            net_pnl   = gross_pnl - cost
            exit_data['gross_pnl'] = round(gross_pnl, 2)
            exit_data['net_pnl']   = round(net_pnl, 2)

        close_strangle_cycle(c["cycle_id"], exit_data)

        # Record PnL in circuit breaker (paper: warns only, live: halts if limit hit)
        self._cb.record_pnl(net_pnl, self.execution_mode)

        emoji = "🟢" if net_pnl >= 0 else "🔴"
        log.info(
            f"[StrangleExecutor] {emoji} CLOSE cycle={self._cycle_count} "
            f"reason={reason} | spot={spot:.0f} "
            f"| CE exit={ce_ltp:.2f}  PE exit={pe_ltp:.2f} "
            f"| gross=₹{gross_pnl:,.0f}  cost=₹{cost}  net=₹{net_pnl:,.0f}"
        )

        self._active_cycle = None

    # ── Main entry point called each poll cycle ───────────────────────────────

    def run(self, df: pd.DataFrame):
        """
        Called once per poll interval from run_orb.py main loop.
        Handles full lifecycle: entry, touch monitoring, adjustment, EOD close.
        """
        try:
            self._reset_if_new_day()
            today = _now_ist().date()
            t     = _ist_time()

            # Outside trading hours
            if t < dtime(9, 15) or t > SC.STRANGLE_EXIT_TIME:
                return

            # Day already fully closed
            if self._done:
                return

            # Circuit breaker — stop new entries if daily loss limit hit
            if self._cb.is_tripped(self.execution_mode) and not self._active_cycle:
                log.info(
                    f"[StrangleExecutor] Circuit breaker tripped — "
                    f"no new entries. Status: {self._cb.status}"
                )
                self._done = True
                return

            # ── Day-level filters (checked once before entry) ──────────────
            if not self._entered:
                # Day of week filter
                if today.weekday() not in SC.TRADE_WEEKDAYS:
                    log.info(
                        f"[StrangleExecutor] Skip — "
                        f"weekday={today.strftime('%A')} not in trade days"
                    )
                    self._done = True
                    return

                # Wait until 10:00 AM bar
                if t < SC.STRANGLE_ENTRY_TIME:
                    return

                # ORB containment
                if not self._check_orb_containment(df, today):
                    log.info("[StrangleExecutor] Skip — spot outside ORB at 10:00")
                    self._done = True
                    return

                # ATR filter
                today_df = df[df.index.date == today]
                atr_pct  = _compute_atr_pct(today_df.tail(SC.ATR_PERIOD + 5))
                if atr_pct > SC.ATR_MAX_PCT:
                    log.info(
                        f"[StrangleExecutor] Skip — ATR {atr_pct:.4f}% "
                        f"> {SC.ATR_MAX_PCT}%"
                    )
                    self._done = True
                    return

                # All filters passed — open initial strangle
                today_df   = df[df.index.date == today]
                entry_bars = today_df[today_df.index.time == SC.STRANGLE_ENTRY_TIME]
                if entry_bars.empty:
                    return
                spot_10am  = float(entry_bars.iloc[0]["close"])
                expiry     = _next_expiry(today)

                # VIX from Kite (best effort — not critical)
                vix_val = None
                try:
                    vix_data = self.kite.ltp(["NSE:INDIA VIX"])
                    if vix_data and "NSE:INDIA VIX" in vix_data:
                        vix_val = float(vix_data["NSE:INDIA VIX"]["last_price"])
                except Exception:
                    pass

                ok = self._open_cycle(spot_10am, expiry, atr_pct, vix_val)
                if not ok:
                    self._done = True
                return

            # ── Active cycle monitoring ────────────────────────────────────
            if not self._active_cycle:
                return

            # EOD close
            if t >= SC.STRANGLE_EXIT_TIME:
                spot = _get_nifty_ltp(self.kite)
                if spot is None:
                    today_df = df[df.index.date == today]
                    spot     = float(today_df["close"].iloc[-1]) if not today_df.empty else 0.0
                self._close_cycle(spot, "EOD")
                self._done = True
                return

            # Touch detection — use live spot for responsiveness
            spot = _get_nifty_ltp(self.kite)
            if spot is None:
                today_df = df[df.index.date == today]
                spot     = float(today_df["close"].iloc[-1]) if not today_df.empty else 0.0

            c       = self._active_cycle
            touched = None
            if spot >= c["ce_strike"]:
                touched = "CE_HIT"
            elif spot <= c["pe_strike"]:
                touched = "PE_HIT"

            if not touched:
                log.debug(
                    f"[StrangleExecutor] Monitoring | spot={spot:.0f} "
                    f"CE>{c['ce_strike']} PE<{c['pe_strike']}"
                )
                return

            # Strike touched — close current cycle
            log.info(
                f"[StrangleExecutor] {touched} | spot={spot:.0f} "
                f"CE={c['ce_strike']} PE={c['pe_strike']}"
            )
            self._close_cycle(spot, touched)

            # Adjust if remaining adjustments available
            if self._adj_count < SC.MAX_ADJUSTMENTS:
                self._adj_count += 1
                expiry = c["expiry"]
                if isinstance(expiry, str):
                    from datetime import date as _date
                    expiry = _date.fromisoformat(expiry)

                # Recompute ATR for adjustment entry
                today_df = df[df.index.date == today]
                atr_pct  = _compute_atr_pct(today_df.tail(SC.ATR_PERIOD + 5))

                vix_val = None
                try:
                    vix_data = self.kite.ltp(["NSE:INDIA VIX"])
                    if vix_data and "NSE:INDIA VIX" in vix_data:
                        vix_val = float(vix_data["NSE:INDIA VIX"]["last_price"])
                except Exception:
                    pass

                log.info(
                    f"[StrangleExecutor] Adjusting → "
                    f"cycle {self._cycle_count + 1} "
                    f"({self._adj_count}/{SC.MAX_ADJUSTMENTS} adjustments used)"
                )
                ok = self._open_cycle(spot, expiry, atr_pct, vix_val)
                if not ok:
                    log.warning("[StrangleExecutor] Adjustment entry failed — no more cycles today")
                    self._done = True
            else:
                log.info("[StrangleExecutor] Max adjustments reached — done for day")
                self._done = True

        except Exception as e:
            log.error(f"[StrangleExecutor] run() crashed: {e}", exc_info=True)
