# orb_strategy/strangle_db.py
#
# DB operations for the ORB Short Strangle strategy.
# Reuses existing open_trades / closed_trades tables.
# strategy_name = 'strangle_nifty' distinguishes from 'orb_nifty'.
# Strangle-specific fields (ce_strike, pe_strike, cycle_num etc.)
# are stored in the existing JSONB extra column — same pattern as orb_db.py.
#
# No schema changes required.

from __future__ import annotations
import sys
import json
sys.path.insert(0, "/home/shona_nagpal/trading/Bollinger")

from production.common.db import db_cursor


# ══════════════════════════════════════════════════════════════════════════════
#  OPEN CYCLE
# ══════════════════════════════════════════════════════════════════════════════

def insert_strangle_cycle(cycle: dict):
    """
    Insert an open strangle cycle into open_trades.

    Mapping to existing columns:
        trade_id      → cycle_id (UUID)
        strategy_name → 'strangle_nifty'
        side          → 'STRANGLE'
        entry_price   → avg of (ce_entry_px + pe_entry_px) / 2
        quantity      → units (lots × lot_size)
        stoploss      → NULL (strangle manages risk via adjustment, not SL)
        extra         → all strangle-specific fields as JSONB
    """
    extra = {
        "cycle_num":    cycle["cycle_num"],
        "ce_strike":    cycle["ce_strike"],
        "pe_strike":    cycle["pe_strike"],
        "expiry":       str(cycle["expiry"]),
        "ce_entry_px":  cycle["ce_entry_px"],
        "pe_entry_px":  cycle["pe_entry_px"],
        "spot_entry":   cycle["spot_entry"],
        "lots":         cycle["lots"],
        "vix_at_entry": cycle["vix_at_entry"],
        "atr_pct":      cycle["atr_pct"],
        "adj_count":    cycle["adj_count"],
    }

    avg_premium = (cycle["ce_entry_px"] + cycle["pe_entry_px"]) / 2

    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO open_trades (
                trade_id, signal_id, strategy_name, timeframe,
                symbol, side, execution_mode,
                entry_time, entry_candle_time, entry_price,
                quantity, stoploss, target,
                atr_pct, rr, status, extra
            ) VALUES (
                %(trade_id)s, NULL, 'strangle_nifty', '5minute',
                'NIFTY50', 'STRANGLE', %(execution_mode)s,
                %(entry_time)s, %(entry_time)s, %(avg_premium)s,
                %(quantity)s, NULL, NULL,
                %(atr_pct)s, NULL, 'OPEN', %(extra)s
            )
        """, {
            "trade_id":       cycle["cycle_id"],
            "execution_mode": cycle["execution_mode"],
            "entry_time":     cycle["entry_time"],
            "avg_premium":    round(avg_premium, 2),
            "quantity":       float(cycle["units"]),
            "atr_pct":        cycle["atr_pct"],
            "extra":          json.dumps(extra),
        })


# ══════════════════════════════════════════════════════════════════════════════
#  CLOSE CYCLE
# ══════════════════════════════════════════════════════════════════════════════

def close_strangle_cycle(cycle_id: str, exit_data: dict):
    """
    Move strangle cycle from open_trades → closed_trades.
    Same open → closed pattern as close_orb_trade().
    """
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM open_trades WHERE trade_id = %s", (cycle_id,)
        )
        row = cur.fetchone()
        if not row:
            return

        # Merge exit fields into extra
        existing_extra = row["extra"] or {}
        if isinstance(existing_extra, str):
            existing_extra = json.loads(existing_extra)

        existing_extra.update({
            "ce_exit_px":       exit_data["ce_exit_px"],
            "pe_exit_px":       exit_data["pe_exit_px"],
            "spot_exit":        exit_data["spot_exit"],
            "gross_pnl":        exit_data["gross_pnl"],
            "transaction_cost": exit_data["transaction_cost"],
        })

        avg_exit_px = (exit_data["ce_exit_px"] + exit_data["pe_exit_px"]) / 2

        cur.execute("""
            INSERT INTO closed_trades (
                trade_id, signal_id, strategy_name, timeframe,
                symbol, side, execution_mode,
                entry_time, entry_price, quantity,
                stoploss, target,
                exit_time, exit_price, exit_reason,
                pnl, rr, atr_pct, extra
            ) VALUES (
                %(trade_id)s, %(signal_id)s, %(strategy_name)s, %(timeframe)s,
                %(symbol)s, %(side)s, %(execution_mode)s,
                %(entry_time)s, %(entry_price)s, %(quantity)s,
                %(stoploss)s, %(target)s,
                %(exit_time)s, %(exit_price)s, %(exit_reason)s,
                %(pnl)s, NULL, %(atr_pct)s, %(extra)s
            )
        """, {
            "trade_id":      cycle_id,
            "signal_id":     row["signal_id"],
            "strategy_name": row["strategy_name"],
            "timeframe":     row["timeframe"],
            "symbol":        row["symbol"],
            "side":          row["side"],
            "execution_mode":row["execution_mode"],
            "entry_time":    row["entry_time"],
            "entry_price":   row["entry_price"],
            "quantity":      row["quantity"],
            "stoploss":      row["stoploss"],
            "target":        row["target"],
            "exit_time":     exit_data["exit_time"],
            "exit_price":    round(avg_exit_px, 2),
            "exit_reason":   exit_data["exit_reason"],  # EOD / CE_HIT / PE_HIT
            "pnl":           exit_data["net_pnl"],       # net after costs
            "atr_pct":       row["atr_pct"],
            "extra":         json.dumps(existing_extra),
        })

        cur.execute(
            "DELETE FROM open_trades WHERE trade_id = %s", (cycle_id,)
        )


# ══════════════════════════════════════════════════════════════════════════════
#  QUERY HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def get_open_strangle_cycles(execution_mode: str) -> list[dict]:
    """Return all open strangle cycles for today."""
    with db_cursor() as cur:
        cur.execute("""
            SELECT * FROM open_trades
            WHERE strategy_name  = 'strangle_nifty'
              AND execution_mode = %s
              AND status         = 'OPEN'
              AND entry_time::date = CURRENT_DATE
            ORDER BY entry_time ASC
        """, (execution_mode,))
        rows = cur.fetchall()
        return [dict(r) for r in rows] if rows else []


def get_today_strangle_cycle_count(execution_mode: str) -> int:
    """Count all strangle cycles today (open + closed) — for adjustment tracking."""
    with db_cursor() as cur:
        # Open cycles today
        cur.execute("""
            SELECT COUNT(*) FROM open_trades
            WHERE strategy_name  = 'strangle_nifty'
              AND execution_mode = %s
              AND entry_time::date = CURRENT_DATE
        """, (execution_mode,))
        open_count = cur.fetchone()[0]

        # Closed cycles today
        cur.execute("""
            SELECT COUNT(*) FROM closed_trades
            WHERE strategy_name  = 'strangle_nifty'
              AND execution_mode = %s
              AND entry_time::date = CURRENT_DATE
        """, (execution_mode,))
        closed_count = cur.fetchone()[0]

        return open_count + closed_count


def strangle_traded_today(execution_mode: str) -> bool:
    """True if any strangle cycle was opened today."""
    return get_today_strangle_cycle_count(execution_mode) > 0
