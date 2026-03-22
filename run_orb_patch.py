# ════════════════════════════════════════════════════════════════════════════
#  PATCH for run_orb.py — integrate strangle strategy
#  Apply these changes exactly. Nothing else in run_orb.py changes.
# ════════════════════════════════════════════════════════════════════════════

# ── CHANGE 1: Add import at top of file (after existing imports) ─────────────
#
# ADD this line after the existing orb_db imports:
#
#   from orb_strategy.strangle_executor import StrangleExecutor
#
# Result should look like:
#
#   from orb_strategy.orb_db import (
#       create_tables,
#       ...
#   )
#   from orb_strategy.strangle_executor import StrangleExecutor   ← ADD THIS


# ── CHANGE 2: Instantiate StrangleExecutor in main() ────────────────────────
#
# FIND this block in main():
#
#   signal_engine = ORBSignal()
#   executor      = ORBExecutor(execution_mode)
#
# CHANGE TO:
#
#   signal_engine     = ORBSignal()
#   executor          = ORBExecutor(execution_mode)
#   strangle_executor = StrangleExecutor(_get_kite(), execution_mode)   ← ADD


# ── CHANGE 3: Call strangle in the main loop ─────────────────────────────────
#
# FIND this block in the while True loop:
#
#   executor.monitor(df)
#
#   if not executor.active_trade:
#       signal = signal_engine.check(df)
#       if signal:
#           executor.enter(signal)
#
# CHANGE TO:
#
#   executor.monitor(df)
#
#   if not executor.active_trade:
#       signal = signal_engine.check(df)
#       if signal:
#           executor.enter(signal)
#
#   if not signal_engine.signal_fired or strangle_executor._active_cycle is not None:
#       strangle_executor.run(df)                         ← ADD
#
# Gate logic explained:
#   signal_fired=False                    → ORB never triggered → strangle enters/monitors freely
#   signal_fired=True, _active_cycle set  → ORB fired AFTER strangle entered at 10:00
#                                           → keep monitoring open strangle to EOD close
#   signal_fired=True, _active_cycle=None → ORB fired, no open strangle → skip entirely
#
# This prevents orphaning an open strangle position when ORB fires at 10:30+.
# The ORB code has zero knowledge of the strangle — gate is one-way only.


# ════════════════════════════════════════════════════════════════════════════
#  FINAL STATE of the relevant section of run_orb.py after patch:
# ════════════════════════════════════════════════════════════════════════════

"""
# --- imports (top of file) ---
from orb_strategy.orb_db import (
    create_tables,
    insert_orb_trade,
    close_orb_trade,
    get_open_orb_trade,
    upsert_orb_state,
    get_orb_state,
)
from orb_strategy.strangle_executor import StrangleExecutor   # ← ADDED


# --- main() ---
def main(execution_mode: str = "PAPER"):
    log.info("=" * 60)
    log.info(f"  Nifty ORB Options Executor  [{execution_mode}]")
    log.info("=" * 60)

    create_tables()

    signal_engine     = ORBSignal()
    executor          = ORBExecutor(execution_mode)
    strangle_executor = StrangleExecutor(_get_kite(), execution_mode)  # ← ADDED

    open_trade = get_open_orb_trade(execution_mode)
    if open_trade:
        executor.active_trade = dict(open_trade)
        log.info(...)

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

            if not signal_engine.signal_fired or strangle_executor._active_cycle is not None:
                strangle_executor.run(df)               # ← ADDED

        except KeyboardInterrupt:
            log.info("[Main] Shutting down.")
            break
        except Exception as e:
            log.error(f"[Main] Unhandled error: {e}", exc_info=True)

        time.sleep(C.POLL_INTERVAL_SEC)
"""


# ════════════════════════════════════════════════════════════════════════════
#  PATCH for run_orb.py — ORB executor: order placement + circuit breaker
#  These are ADDITIONAL changes to apply to run_orb.py on top of the
#  strangle integration patch above.
# ════════════════════════════════════════════════════════════════════════════

# ── CHANGE 4: Add imports at top of run_orb.py ───────────────────────────────
#
# ADD after existing imports:
#
#   from orb_strategy.circuit_breaker    import CircuitBreaker
#   from orb_strategy.live_order_manager import sell_option, buy_option, reconcile_positions


# ── CHANGE 5: Add circuit breaker to ORBExecutor.__init__ ────────────────────
#
# FIND in ORBExecutor.__init__:
#   self.active_trade   = None
#
# CHANGE TO:
#   self.active_trade   = None
#   self._cb            = CircuitBreaker("orb_nifty", limit=20_000)


# ── CHANGE 6: Add reconciliation on startup in main() ────────────────────────
#
# FIND in main():
#   open_trade = get_open_orb_trade(execution_mode)
#   if open_trade:
#       executor.active_trade = dict(open_trade)
#       log.info(...)
#
# CHANGE TO:
#   open_trade = get_open_orb_trade(execution_mode)
#   if open_trade:
#       executor.active_trade = dict(open_trade)
#       log.info(...)
#
#   # LIVE: reconcile DB state against actual Kite positions on startup
#   if execution_mode == "LIVE":
#       open_trades = [dict(open_trade)] if open_trade else []
#       if not reconcile_positions(_get_kite(), open_trades):
#           log.critical("[Main] Reconciliation failed — starting in safe mode (no new entries)")
#           signal_engine.signal_fired = True   # block ORB entries
#           strangle_executor._done    = True   # block strangle entries


# ── CHANGE 7: Wire order placement into ORBExecutor.enter() ──────────────────
#
# FIND in ORBExecutor.enter():
#   insert_orb_trade(trade)
#   self.active_trade = trade
#
# CHANGE TO:
#   # LIVE: place actual sell order on exchange
#   if self.execution_mode == "LIVE":
#       fill = sell_option(
#           _get_kite(), trade["expiry"], trade["strike"],
#           trade["direction"], trade["units"]
#       )
#       if not fill:
#           log.error("[ORBExecutor] LIVE entry failed — order not filled — aborting")
#           return
#       trade["premium_entry"] = round(fill["avg_price"], 2)
#       trade["cost_outflow"]  = round(fill["avg_price"] * trade["units"], 2)
#
#   insert_orb_trade(trade)
#   self.active_trade = trade


# ── CHANGE 8: Wire order placement into ORBExecutor.monitor() exit ───────────
#
# FIND in ORBExecutor.monitor(), just before close_orb_trade():
#   exit_data = { ... }
#   close_orb_trade(trade_id, exit_data)
#
# ADD between exit_data construction and close_orb_trade():
#
#   # LIVE: place actual buy order to close position
#   if self.execution_mode == "LIVE":
#       fill = buy_option(
#           _get_kite(), expiry, strike, direction, self.active_trade["units"]
#       )
#       if fill:
#           price_exit = fill["avg_price"]
#           exit_data["premium_exit"] = round(price_exit, 2)
#           pnl     = (price_exit - self.active_trade["premium_entry"]) * units
#           pnl_pct = (price_exit - self.active_trade["premium_entry"]) / self.active_trade["premium_entry"] * 100
#           exit_data["pnl"]     = round(pnl, 2)
#           exit_data["pnl_pct"] = round(pnl_pct, 2)
#
#   close_orb_trade(trade_id, exit_data)
#
#   # Record PnL in circuit breaker
#   executor._cb.record_pnl(exit_data["pnl"], self.execution_mode)


# ── CHANGE 9: Add circuit breaker gate in ORBExecutor.enter() ────────────────
#
# FIND in ORBExecutor.enter():
#   if self.active_trade:
#       log.warning("[ORBExecutor] Already in trade — skipping entry")
#       return
#
# CHANGE TO:
#   if self.active_trade:
#       log.warning("[ORBExecutor] Already in trade — skipping entry")
#       return
#
#   if self._cb.is_tripped(self.execution_mode):
#       log.info(f"[ORBExecutor] Circuit breaker tripped — no entry. {self._cb.status}")
#       return


# ── CHANGE 10: Fix build_symbol() in run_orb.py ──────────────────────────────
#
# FIND the existing build_symbol function:
#
#   def build_symbol(expiry: date, strike: int, opt_type: str) -> str:
#       """
#       Build Kite NFO tradingsymbol for NIFTY weekly options.
#       Format: NIFTY{YY}{MM}{DD}{strike}{CE/PE}
#       Example: NIFTY2632423800CE for 2026-03-24 strike 23800 CE
#       """
#       return f"NFO:NIFTY{expiry.strftime('%y%m%d')}{strike}{opt_type}"
#
# REPLACE WITH:
#
#   def build_symbol(expiry: date, strike: int, opt_type: str) -> str:
#       """
#       Kite NFO tradingsymbol for Nifty weekly options.
#       Format: NIFTY{YY}{M}{DD}{strike}{CE/PE}
#         YY = 2-digit year, M = month no leading zero, DD = day with leading zero
#       Example: NIFTY2632423100CE = expiry 2026-03-24, strike 23100, CE
#       Verified against live Kite instruments on 2026-03-22.
#       """
#       yy = expiry.strftime('%y')
#       m  = str(expiry.month)      # no leading zero
#       dd = expiry.strftime('%d')  # with leading zero
#       return f"NFO:NIFTY{yy}{m}{dd}{strike}{opt_type}"
