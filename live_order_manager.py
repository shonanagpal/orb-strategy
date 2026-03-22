# orb_strategy/live_order_manager.py
#
# Handles all live order placement for both ORB and Strangle strategies.
# Paper mode: this module is never called.
# Live mode: all Kite order interactions go through here.
#
# Responsibilities:
#   - Place MKT orders on NFO with retry (up to 3 attempts, 5s gap)
#   - Confirm fill price and quantity from order history
#   - Position reconciliation on startup
#   - Never touches DB — callers handle DB writes

from __future__ import annotations

import time
import logging
from datetime import date
from typing import Optional

log = logging.getLogger("orb")

# ── Constants ────────────────────────────────────────────────────────────────
MAX_RETRIES    = 3
RETRY_DELAY_S  = 5
EXCHANGE       = "NFO"
PRODUCT        = "MIS"    # intraday margin product
ORDER_TYPE     = "MARKET"
VARIETY        = "regular"


# ══════════════════════════════════════════════════════════════════════════════
#  SYMBOL BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_nfo_symbol(expiry: date, strike: int, opt_type: str) -> str:
    """
    Kite NFO tradingsymbol for Nifty weekly options.
    Format: NIFTY{YY}{M}{DD}{strike}{CE/PE}
      YY  = 2-digit year
      M   = month without leading zero (1-12)
      DD  = day with leading zero (01-31)
    Example: NIFTY2632423100CE = expiry 2026-03-24, strike 23100, CE
    Verified against live Kite instruments on 2026-03-22.
    """
    yy = expiry.strftime('%y')
    m  = str(expiry.month)        # no leading zero
    dd = expiry.strftime('%d')    # with leading zero
    return f"NFO:NIFTY{yy}{m}{dd}{strike}{opt_type}"


# ══════════════════════════════════════════════════════════════════════════════
#  CORE ORDER PLACER
# ══════════════════════════════════════════════════════════════════════════════

def _place_order(
    kite,
    tradingsymbol: str,
    transaction_type: str,   # kite.TRANSACTION_TYPE_BUY or SELL
    quantity: int,
) -> Optional[str]:
    """
    Place a single MKT order. Returns order_id on success, None on failure.
    Does NOT retry — retry logic is in the callers.
    """
    try:
        order_id = kite.place_order(
            variety          = VARIETY,
            exchange         = EXCHANGE,
            tradingsymbol    = tradingsymbol,
            transaction_type = transaction_type,
            quantity         = quantity,
            product          = PRODUCT,
            order_type       = ORDER_TYPE,
        )
        log.info(
            f"[OrderMgr] Placed {transaction_type} {quantity} {tradingsymbol} "
            f"→ order_id={order_id}"
        )
        return order_id
    except Exception as e:
        log.warning(f"[OrderMgr] Place order failed: {tradingsymbol} {transaction_type} — {e}")
        return None


def _get_fill(kite, order_id: str) -> dict | None:
    """
    Poll order history for fill details.
    Returns dict with avg_price and filled_quantity, or None if not filled.
    """
    try:
        history = kite.order_history(order_id)
        if not history:
            return None
        # Last entry has the most recent status
        last = history[-1]
        status = last.get("status", "")
        if status == "COMPLETE":
            return {
                "avg_price":        float(last.get("average_price", 0)),
                "filled_quantity":  int(last.get("filled_quantity", 0)),
                "status":           status,
            }
        if status in ("REJECTED", "CANCELLED"):
            log.warning(
                f"[OrderMgr] Order {order_id} {status}: "
                f"{last.get('status_message', '')}"
            )
        return None
    except Exception as e:
        log.warning(f"[OrderMgr] order_history failed for {order_id}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC: SELL (entry for short options)
# ══════════════════════════════════════════════════════════════════════════════

def sell_option(
    kite,
    expiry: date,
    strike: int,
    opt_type: str,
    quantity: int,
) -> dict | None:
    """
    Sell (short) an option. Retries up to MAX_RETRIES times.
    Returns fill dict {avg_price, filled_quantity} or None if all retries fail.
    """
    symbol = build_nfo_symbol(expiry, strike, opt_type)
    remaining = quantity

    for attempt in range(1, MAX_RETRIES + 1):
        log.info(
            f"[OrderMgr] SELL attempt {attempt}/{MAX_RETRIES} — "
            f"{symbol} qty={remaining}"
        )
        order_id = _place_order(
            kite, symbol, kite.TRANSACTION_TYPE_SELL, remaining
        )
        if not order_id:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_S)
            continue

        # Wait briefly then check fill
        time.sleep(2)
        fill = _get_fill(kite, order_id)

        if fill and fill["filled_quantity"] == remaining:
            log.info(
                f"[OrderMgr] SELL filled: {symbol} "
                f"qty={fill['filled_quantity']} @ ₹{fill['avg_price']:.2f}"
            )
            return fill

        if fill and fill["filled_quantity"] > 0:
            # Partial fill — retry remaining
            filled = fill["filled_quantity"]
            log.warning(
                f"[OrderMgr] Partial fill: {symbol} "
                f"{filled}/{remaining} filled @ ₹{fill['avg_price']:.2f} — retrying remainder"
            )
            remaining -= filled
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_S)
            continue

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_S)

    log.error(
        f"[OrderMgr] SELL failed after {MAX_RETRIES} attempts: {symbol} qty={quantity}"
    )
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC: BUY (exit for short options — buy to close)
# ══════════════════════════════════════════════════════════════════════════════

def buy_option(
    kite,
    expiry: date,
    strike: int,
    opt_type: str,
    quantity: int,
) -> dict | None:
    """
    Buy (to close) an option. Retries up to MAX_RETRIES times.
    Returns fill dict {avg_price, filled_quantity} or None if all retries fail.
    """
    symbol = build_nfo_symbol(expiry, strike, opt_type)
    remaining = quantity

    for attempt in range(1, MAX_RETRIES + 1):
        log.info(
            f"[OrderMgr] BUY attempt {attempt}/{MAX_RETRIES} — "
            f"{symbol} qty={remaining}"
        )
        order_id = _place_order(
            kite, symbol, kite.TRANSACTION_TYPE_BUY, remaining
        )
        if not order_id:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_S)
            continue

        time.sleep(2)
        fill = _get_fill(kite, order_id)

        if fill and fill["filled_quantity"] == remaining:
            log.info(
                f"[OrderMgr] BUY filled: {symbol} "
                f"qty={fill['filled_quantity']} @ ₹{fill['avg_price']:.2f}"
            )
            return fill

        if fill and fill["filled_quantity"] > 0:
            filled = fill["filled_quantity"]
            log.warning(
                f"[OrderMgr] Partial fill: {symbol} "
                f"{filled}/{remaining} filled @ ₹{fill['avg_price']:.2f} — retrying remainder"
            )
            remaining -= filled
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_S)
            continue

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_S)

    log.error(
        f"[OrderMgr] BUY failed after {MAX_RETRIES} attempts: {symbol} qty={quantity}"
    )
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  POSITION RECONCILIATION
# ══════════════════════════════════════════════════════════════════════════════

def reconcile_positions(kite, open_db_trades: list[dict]) -> bool:
    """
    On startup in LIVE mode, cross-check DB open trades against
    actual Kite positions. Returns True if everything matches.

    If mismatch found: logs CRITICAL and returns False.
    Caller should halt new entries until manually resolved.
    """
    if not open_db_trades:
        # No open DB trades — verify no orphan positions on exchange either
        try:
            positions = kite.positions()
            day_pos   = positions.get("day", [])
            nifty_pos = [
                p for p in day_pos
                if "NIFTY" in p.get("tradingsymbol", "")
                and p.get("quantity", 0) != 0
            ]
            if nifty_pos:
                log.critical(
                    f"[OrderMgr] RECONCILIATION MISMATCH: "
                    f"DB has no open trades but Kite has {len(nifty_pos)} "
                    f"open Nifty position(s): "
                    f"{[p['tradingsymbol'] for p in nifty_pos]} — "
                    f"HALTING new entries. Resolve manually."
                )
                return False
        except Exception as e:
            log.warning(f"[OrderMgr] Position fetch failed during reconciliation: {e}")
            # Non-fatal — proceed cautiously
        return True

    # DB has open trades — verify they exist on exchange
    try:
        positions   = kite.positions()
        day_pos     = positions.get("day", [])
        pos_by_sym  = {
            p["tradingsymbol"]: p for p in day_pos
            if p.get("quantity", 0) != 0
        }

        all_ok = True
        for trade in open_db_trades:
            import json
            extra = trade.get("extra") or {}
            if isinstance(extra, str):
                extra = json.loads(extra)

            strategy = trade.get("strategy_name", "")

            if strategy == "orb_nifty":
                strike  = extra.get("strike")
                expiry  = extra.get("expiry")
                side    = trade.get("side")
                if strike and expiry and side:
                    from datetime import date as _date
                    exp = _date.fromisoformat(expiry) if isinstance(expiry, str) else expiry
                    sym = build_nfo_symbol(exp, int(strike), side)
                    if sym not in pos_by_sym:
                        log.critical(
                            f"[OrderMgr] RECONCILIATION MISMATCH: "
                            f"DB has open ORB trade {sym} but NOT found in Kite positions — "
                            f"HALTING new entries. Resolve manually."
                        )
                        all_ok = False

            elif strategy == "strangle_nifty":
                ce_strike = extra.get("ce_strike")
                pe_strike = extra.get("pe_strike")
                expiry    = extra.get("expiry")
                if ce_strike and pe_strike and expiry:
                    from datetime import date as _date
                    exp    = _date.fromisoformat(expiry) if isinstance(expiry, str) else expiry
                    ce_sym = build_nfo_symbol(exp, int(ce_strike), "CE")
                    pe_sym = build_nfo_symbol(exp, int(pe_strike), "PE")
                    for sym in [ce_sym, pe_sym]:
                        if sym not in pos_by_sym:
                            log.critical(
                                f"[OrderMgr] RECONCILIATION MISMATCH: "
                                f"DB has open strangle leg {sym} but NOT found in Kite — "
                                f"HALTING new entries. Resolve manually."
                            )
                            all_ok = False

        return all_ok

    except Exception as e:
        log.warning(f"[OrderMgr] Reconciliation check failed: {e} — proceeding cautiously")
        return True   # non-fatal, don't block trading on API error
