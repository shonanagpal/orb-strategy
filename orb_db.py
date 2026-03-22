# orb_strategy/orb_db.py
# Uses existing open_trades + closed_trades tables.
# ORB-specific fields stored in JSONB extra column.
# Existing equity strategies unaffected (extra = NULL for them).

from __future__ import annotations
import sys
import json
sys.path.insert(0, "/home/shona_nagpal/trading/Bollinger")

from production.common.db import db_cursor


# ══════════════════════════════════════════════════════════════════════════════
#  ORB STATE TABLE (still separate — tiny, strategy-specific)
# ══════════════════════════════════════════════════════════════════════════════

def create_tables():
    """Create orb_state table only. Trades go into existing tables."""
    with db_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orb_state (
                trade_date    DATE PRIMARY KEY,
                orb_high      NUMERIC(10,2),
                orb_low       NUMERIC(10,2),
                orb_range_pct NUMERIC(8,4),
                orb_formed    BOOLEAN DEFAULT FALSE,
                signal_fired  BOOLEAN DEFAULT FALSE,
                direction     VARCHAR(2),
                regime        VARCHAR(4),
                created_at    TIMESTAMP DEFAULT NOW()
            );
        """)
    print("[orb_db] Tables ready (orb_state + existing open/closed_trades).")


# ══════════════════════════════════════════════════════════════════════════════
#  TRADE OPERATIONS — using existing open_trades / closed_trades
# ══════════════════════════════════════════════════════════════════════════════

def insert_orb_trade(trade: dict):
    """
    Insert ORB entry into open_trades.
    Core fields map to existing columns.
    ORB-specific fields (strike, expiry, Greeks etc.) go into extra JSONB.
    """
    extra = {
        "direction":           trade["direction"],
        "regime":              trade["regime"],
        "orb_high":            trade["orb_high"],
        "orb_low":             trade["orb_low"],
        "orb_range_pct":       trade["orb_range_pct"],
        "prior_day_range_pct": trade["prior_day_range_pct"],
        "strike":              trade["strike"],
        "expiry":              str(trade["expiry"]),
        "dte_at_entry":        trade["dte_at_entry"],
        "spot_entry":          trade["spot_entry"],
        "vix_on_entry":        trade["vix_on_entry"],
        "iv_entry_pct":        trade["iv_entry_pct"],
        "lots":                trade["lots"],
        "cost_outflow":        trade["cost_outflow"],
        "delta_entry":         trade["delta_entry"],
        "gamma_entry":         trade["gamma_entry"],
        "theta_entry_per_day": trade["theta_entry_per_day"],
        "vega_entry_per_1pct": trade["vega_entry_per_1pct"],
    }

    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO open_trades (
                trade_id, signal_id, strategy_name, timeframe,
                symbol, side, execution_mode,
                entry_time, entry_candle_time, entry_price,
                quantity, stoploss, target,
                atr_pct, rr, status, extra
            ) VALUES (
                %(trade_id)s, NULL, 'orb_nifty', '5minute',
                'NIFTY50', %(side)s, %(execution_mode)s,
                %(entry_time)s, %(entry_time)s, %(entry_price)s,
                %(quantity)s, %(stoploss)s, NULL,
                %(atr_pct)s, NULL, 'OPEN', %(extra)s
            )
        """, {
            "trade_id":       trade["trade_id"],
            "side":           trade["direction"],        # CE or PE
            "execution_mode": trade["execution_mode"],
            "entry_time":     trade["entry_time"],
            "entry_price":    trade["premium_entry"],    # option premium
            "quantity":       float(trade["units"]),     # lots × lot_size
            "stoploss":       trade["sl_spot_level"],    # spot SL level
            "atr_pct":        trade["atr_pct_at_entry"],
            "extra":          json.dumps(extra),
        })


def close_orb_trade(trade_id: str, exit_data: dict):
    """
    Move ORB trade from open_trades → closed_trades.
    Matches same pattern as existing exit_engine.py.
    """
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM open_trades WHERE trade_id = %s", (trade_id,)
        )
        row = cur.fetchone()
        if not row:
            return

        existing_extra = row["extra"] or {}
        if isinstance(existing_extra, str):
            existing_extra = json.loads(existing_extra)

        existing_extra.update({
            "spot_exit":    exit_data["spot_exit"],
            "spot_chg_pct": exit_data["spot_chg_pct"],
            "iv_exit_pct":  exit_data["iv_exit_pct"],
            "sl_hit":       exit_data["sl_hit"],
        })

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
            "trade_id":       trade_id,
            "signal_id":      row["signal_id"],
            "strategy_name":  row["strategy_name"],
            "timeframe":      row["timeframe"],
            "symbol":         row["symbol"],
            "side":           row["side"],
            "execution_mode": row["execution_mode"],
            "entry_time":     row["entry_time"],
            "entry_price":    row["entry_price"],
            "quantity":       row["quantity"],
            "stoploss":       row["stoploss"],
            "target":         row["target"],
            "exit_time":      exit_data["exit_time"],
            "exit_price":     exit_data["premium_exit"],
            "exit_reason":    exit_data["exit_reason"],  # 'SL_HIT' or 'EOD_EXIT'
            "pnl":            exit_data["pnl"],
            "atr_pct":        row["atr_pct"],
            "extra":          json.dumps(existing_extra),
        })

        cur.execute(
            "DELETE FROM open_trades WHERE trade_id = %s", (trade_id,)
        )


def get_open_orb_trade(execution_mode: str = "PAPER") -> dict | None:
    """
    Returns today's open ORB trade if any.
    Date filter prevents restoring stale trades from prior days on restart.
    """
    with db_cursor() as cur:
        cur.execute("""
            SELECT * FROM open_trades
            WHERE strategy_name  = 'orb_nifty'
              AND execution_mode = %s
              AND status         = 'OPEN'
              AND entry_time::date = CURRENT_DATE
            ORDER BY entry_time DESC LIMIT 1
        """, (execution_mode,))
        return cur.fetchone()


# ══════════════════════════════════════════════════════════════════════════════
#  ORB STATE
# ══════════════════════════════════════════════════════════════════════════════

def upsert_orb_state(state: dict):
    with db_cursor() as cur:
        cur.execute("""
            INSERT INTO orb_state (
                trade_date, orb_high, orb_low, orb_range_pct,
                orb_formed, signal_fired, direction, regime
            ) VALUES (
                %(trade_date)s, %(orb_high)s, %(orb_low)s, %(orb_range_pct)s,
                %(orb_formed)s, %(signal_fired)s, %(direction)s, %(regime)s
            )
            ON CONFLICT (trade_date) DO UPDATE SET
                orb_high      = EXCLUDED.orb_high,
                orb_low       = EXCLUDED.orb_low,
                orb_range_pct = EXCLUDED.orb_range_pct,
                orb_formed    = EXCLUDED.orb_formed,
                signal_fired  = EXCLUDED.signal_fired,
                direction     = EXCLUDED.direction,
                regime        = EXCLUDED.regime
        """, state)


def get_orb_state(trade_date) -> dict | None:
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM orb_state WHERE trade_date = %s", (trade_date,)
        )
        return cur.fetchone()
