# orb_strategy/circuit_breaker.py
#
# Daily loss circuit breaker — Rs.20,000 per strategy independently.
# Checked after every trade close in LIVE mode.
# Paper mode: circuit breaker still tracks PnL but never halts trading.
#
# Usage:
#   cb = CircuitBreaker("orb_nifty",    limit=20_000)
#   cb = CircuitBreaker("strangle_nifty", limit=20_000)
#
#   cb.record_pnl(net_pnl)     # call after every closed trade
#   cb.is_tripped()            # check before opening any new position
#   cb.reset()                 # called automatically on new trading day

from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("orb")

# ── Default limits (can be overridden per-instance) ───────────────────────────
DEFAULT_DAILY_LOSS_LIMIT = 20_000   # Rs.


class CircuitBreaker:
    """
    Tracks cumulative net PnL for one strategy for the current trading day.
    Trips when cumulative loss exceeds the daily limit.
    Resets automatically on a new calendar day.
    """

    def __init__(
        self,
        strategy_name: str,
        limit: float = DEFAULT_DAILY_LOSS_LIMIT,
        live_only: bool = True,
    ):
        self.strategy_name = strategy_name
        self.limit         = limit
        self.live_only     = live_only   # if True, only halts in LIVE mode

        self._today        = None
        self._cumulative   = 0.0
        self._tripped      = False
        self._trip_reason  = None

    def _reset_if_new_day(self):
        today = date.today()
        if self._today != today:
            if self._today is not None:
                log.info(
                    f"[CircuitBreaker:{self.strategy_name}] New day — "
                    f"reset. Yesterday cumulative PnL: ₹{self._cumulative:,.0f}"
                )
            self._today      = today
            self._cumulative = 0.0
            self._tripped    = False
            self._trip_reason = None

    def record_pnl(self, net_pnl: float, execution_mode: str = "PAPER"):
        """
        Record PnL from a closed trade. Trips breaker if limit exceeded.
        Call after every trade close.
        """
        self._reset_if_new_day()
        self._cumulative += net_pnl

        log.info(
            f"[CircuitBreaker:{self.strategy_name}] "
            f"Trade PnL: ₹{net_pnl:,.0f} | "
            f"Day cumulative: ₹{self._cumulative:,.0f} | "
            f"Limit: -₹{self.limit:,.0f}"
        )

        if self._cumulative <= -self.limit and not self._tripped:
            self._tripped     = True
            self._trip_reason = (
                f"Daily loss limit -₹{self.limit:,.0f} breached — "
                f"cumulative PnL ₹{self._cumulative:,.0f}"
            )
            if execution_mode == "LIVE" or not self.live_only:
                log.critical(
                    f"[CircuitBreaker:{self.strategy_name}] 🚨 TRIPPED — "
                    f"{self._trip_reason} — NO NEW ENTRIES for rest of day"
                )
            else:
                log.warning(
                    f"[CircuitBreaker:{self.strategy_name}] ⚠️  Would trip in LIVE mode — "
                    f"{self._trip_reason}"
                )

    def is_tripped(self, execution_mode: str = "PAPER") -> bool:
        """
        Returns True if new entries should be blocked.
        In paper mode: logs warning but never actually blocks (returns False).
        In live mode: returns True when tripped.
        """
        self._reset_if_new_day()
        if not self._tripped:
            return False
        if execution_mode != "LIVE" and self.live_only:
            # Paper mode — warn but don't block
            log.warning(
                f"[CircuitBreaker:{self.strategy_name}] "
                f"Would be blocked in LIVE mode ({self._trip_reason})"
            )
            return False
        return True

    @property
    def cumulative_pnl(self) -> float:
        self._reset_if_new_day()
        return self._cumulative

    @property
    def status(self) -> str:
        self._reset_if_new_day()
        if self._tripped:
            return f"TRIPPED ({self._trip_reason})"
        return f"OK (cumulative ₹{self._cumulative:,.0f} / limit -₹{self.limit:,.0f})"
