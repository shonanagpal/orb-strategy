#!/usr/bin/env python3
# validate_day.py
#
# Daily validation: signals generated, trades executed, PnL summary
# Works for ANY date via --date parameter
#
# Usage:
#   source .env.kite && python3 validate_day.py
#   source .env.kite && python3 validate_day.py --date 2026-03-14

from production.common.db import db_cursor
import datetime as dt
import argparse
import json

# ─────────────────────────────────────────────────────────────────────
# CLI ARGUMENTS
# ─────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Daily trading report")
parser.add_argument(
    "--date",
    type=str,
    help="Date to generate report for (YYYY-MM-DD). Default: today"
)

args = parser.parse_args()

report_date = (
    dt.datetime.strptime(args.date, "%Y-%m-%d").date()
    if args.date
    else dt.date.today()
)

if report_date > dt.date.today():
    raise ValueError("Report date cannot be in the future")

# ─────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────

def fmt_time(t):
    return t.strftime("%H:%M") if t else "N/A"

def fmt_time_sec(t):
    return t.strftime("%H:%M:%S") if t else "N/A"

def parse_extra(extra):
    if not extra:
        return {}
    if isinstance(extra, dict):
        return extra
    try:
        return json.loads(extra)
    except Exception:
        return {}

# ─────────────────────────────────────────────────────────────────────

print("=" * 70)
print(f"  DAILY TRADING REPORT — {report_date}")
print("=" * 70)

# ─────────────────────────────────────────────────────────────────────
# 1. SIGNALS GENERATED
# ─────────────────────────────────────────────────────────────────────

print("\n📊 SIGNALS GENERATED (Equity)")
print("─" * 70)

with db_cursor() as cur:
    cur.execute("""
        SELECT 
            symbol, strategy_name, side,
            signal_time_ist, entry_time_exec_ist,
            signal_status, created_at_ist
        FROM signals
        WHERE DATE(created_at_ist) = %s
        ORDER BY created_at_ist
    """, (report_date,))
    signals = cur.fetchall()

if not signals:
    print("No signals generated")
else:
    print(f"Total signals: {len(signals)}\n")

    for s in signals:
        s = dict(s)

        print(f"{s['symbol']:12} {s['side']:5} | {s['strategy_name'][:20]:20}")
        print(f"  Signal time:  {fmt_time(s['signal_time_ist'])}")
        print(f"  Entry window: {fmt_time(s['entry_time_exec_ist'])}")
        print(f"  Status:       {s['signal_status']}")
        print(f"  Created:      {fmt_time_sec(s['created_at_ist'])}")
        print()

# ─────────────────────────────────────────────────────────────────────
# 2. SIGNAL STATUS BREAKDOWN
# ─────────────────────────────────────────────────────────────────────

print("\n📈 SIGNAL STATUS BREAKDOWN")
print("─" * 70)

with db_cursor() as cur:
    cur.execute("""
        SELECT signal_status, COUNT(*) as cnt
        FROM signals
        WHERE DATE(created_at_ist) = %s
        GROUP BY signal_status
        ORDER BY cnt DESC
    """, (report_date,))
    status_breakdown = cur.fetchall()

if not status_breakdown:
    print("No signals")
else:
    for row in status_breakdown:
        r = dict(row)
        print(f"{r['signal_status']:30} {r['cnt']:3}")

# ─────────────────────────────────────────────────────────────────────
# 3. EQUITY TRADES ENTERED
# ─────────────────────────────────────────────────────────────────────

print("\n💼 EQUITY TRADES ENTERED")
print("─" * 70)

with db_cursor() as cur:
    cur.execute("""
        SELECT symbol, side, entry_price, quantity,
               stoploss, target, status, entry_time, execution_mode
        FROM open_trades
        WHERE DATE(created_at) = %s
          AND strategy_name != 'orb_nifty'
        ORDER BY entry_time
    """, (report_date,))
    open_trades = cur.fetchall()

if not open_trades:
    print("No equity trades entered")
else:
    print(f"Total: {len(open_trades)}\n")

    for t in open_trades:
        t = dict(t)

        print(f"{t['symbol']:12} {t['side']:5} [{t['execution_mode']}]")
        print(f"  Entry:  {t['entry_price']:.2f} @ {fmt_time(t['entry_time'])} | Qty: {int(t['quantity'])}")
        print(f"  SL:     {t['stoploss']:.2f}")
        print(f"  Target: {t['target']:.2f}")
        print(f"  Status: {t['status']}")
        print()

# ─────────────────────────────────────────────────────────────────────
# 4. ORB OPTIONS TRADE
# ─────────────────────────────────────────────────────────────────────

print("\n🎯 ORB NIFTY OPTIONS")
print("─" * 70)

with db_cursor() as cur:
    cur.execute("""
        SELECT * FROM orb_state
        WHERE trade_date = %s
    """, (report_date,))
    orb_state = cur.fetchone()

if orb_state:
    orb_state = dict(orb_state)

    formed = orb_state.get("orb_formed", False)
    fired = orb_state.get("signal_fired", False)

    print(f"ORB Formed    : {'✅ Yes' if formed else '⏳ Not yet'}")

    if formed:
        print(f"ORB High      : {orb_state.get('orb_high'):.2f}")
        print(f"ORB Low       : {orb_state.get('orb_low'):.2f}")
        print(f"ORB Range     : {orb_state.get('orb_range_pct'):.3f}%")
        print(f"Regime        : {orb_state.get('regime')}")
        print(f"Signal Fired  : {'✅ Yes — ' + str(orb_state.get('direction')) if fired else '⏳ Watching...'}")
else:
    print("No ORB state for this date")

print()

# ORB open trade

with db_cursor() as cur:
    cur.execute("""
        SELECT * FROM open_trades
        WHERE strategy_name = 'orb_nifty'
          AND DATE(created_at) = %s
        ORDER BY entry_time DESC
        LIMIT 1
    """, (report_date,))
    orb_open = cur.fetchone()

if orb_open:
    orb_open = dict(orb_open)
    extra = parse_extra(orb_open.get("extra"))

    print("📌 OPEN POSITION")

    print(f"  Direction     : {orb_open['side']}")
    print(f"  Strike        : {extra.get('strike','N/A')} {orb_open['side']} | Expiry: {extra.get('expiry','N/A')}")
    print(f"  Spot Entry    : {extra.get('spot_entry','N/A')}")
    print(f"  Premium Entry : ₹{orb_open['entry_price']:.2f}")
    print(f"  Lots          : {extra.get('lots','N/A')} | Units: {int(orb_open['quantity'])}")
    print(f"  Cost          : ₹{extra.get('cost_outflow',0):,.0f}")
    print(f"  SL Level      : {orb_open['stoploss']} (spot)")
    print(f"  Delta         : {extra.get('delta_entry','N/A')}")
    print(f"  IV at Entry   : {extra.get('iv_entry_pct','N/A')}%")
    print(f"  Entry Time    : {fmt_time_sec(orb_open['entry_time'])}")
    print(f"  Execution     : [{orb_open['execution_mode']}]")

# ORB closed trade

with db_cursor() as cur:
    cur.execute("""
        SELECT * FROM closed_trades
        WHERE strategy_name = 'orb_nifty'
          AND DATE(exit_time) = %s
        ORDER BY exit_time DESC
        LIMIT 1
    """, (report_date,))
    orb_closed = cur.fetchone()

if orb_closed:
    orb_closed = dict(orb_closed)
    extra = parse_extra(orb_closed.get("extra"))

    pnl = orb_closed.get("pnl") or 0
    emoji = "✅" if pnl > 0 else "❌"

    print(f"\n{emoji} CLOSED TRADE")

    print(f"  Direction     : {orb_closed['side']}")
    print(f"  Strike        : {extra.get('strike','N/A')} | Expiry: {extra.get('expiry','N/A')}")
    print(f"  Spot Entry    : {extra.get('spot_entry','N/A')} → Exit: {extra.get('spot_exit','N/A')}")
    print(f"  Premium       : ₹{orb_closed['entry_price']:.2f} → ₹{orb_closed['exit_price']:.2f}")
    print(f"  Exit Reason   : {orb_closed.get('exit_reason','')}")
    print(f"  PnL           : ₹{pnl:,.0f}")
    print(f"  Entry → Exit  : {fmt_time(orb_closed['entry_time'])} → {fmt_time(orb_closed['exit_time'])}")
    print(f"  IV Entry/Exit : {extra.get('iv_entry_pct','N/A')}% → {extra.get('iv_exit_pct','N/A')}%")
    print(f"  SL Hit        : {'Yes' if extra.get('sl_hit') else 'No'}")

if not orb_open and not orb_closed:
    print("No ORB trade")

# ─────────────────────────────────────────────────────────────────────
# 5. EQUITY TRADES CLOSED
# ─────────────────────────────────────────────────────────────────────

print("\n💰 EQUITY TRADES CLOSED")
print("─" * 70)

with db_cursor() as cur:
    cur.execute("""
        SELECT symbol, side, entry_price, exit_price,
               exit_reason, pnl, r_multiple,
               entry_time, exit_time, execution_mode
        FROM closed_trades
        WHERE DATE(exit_time) = %s
          AND strategy_name != 'orb_nifty'
        ORDER BY exit_time
    """, (report_date,))
    closed_trades = cur.fetchall()

if not closed_trades:
    print("No equity trades closed")
else:
    print(f"Total: {len(closed_trades)}\n")

    total_pnl = 0
    total_r = 0
    wins = losses = 0

    for t in closed_trades:
        t = dict(t)

        pnl = t["pnl"] or 0
        r = t["r_multiple"] or 0

        total_pnl += pnl
        total_r += r

        if pnl > 0:
            wins += 1
            emoji = "✅"
        else:
            losses += 1
            emoji = "❌"

        print(f"{emoji} {t['symbol']:12} {t['side']:5} [{t['execution_mode']}]")
        print(f"  Entry:  {t['entry_price']:.2f} @ {fmt_time(t['entry_time'])}")
        print(f"  Exit:   {t['exit_price']:.2f} @ {fmt_time(t['exit_time'])}")
        print(f"  Reason: {t['exit_reason']}")
        print(f"  PnL:    ₹{pnl:.2f} ({r:.2f}R)")
        print()

    print("─" * 70)

    n = wins + losses

    print(f"Total PnL:    ₹{total_pnl:.2f}")
    print(f"Total R:      {total_r:.2f}R")
    print(f"Win rate:     {wins}/{n} ({100*wins/n if n else 0:.1f}%)")
    print(f"Avg R/trade:  {total_r/n if n else 0:.2f}R")

# ─────────────────────────────────────────────────────────────────────
# 6. SUMMARY
# ─────────────────────────────────────────────────────────────────────

print("\n" + "=" * 70)
print("  SUMMARY")
print("=" * 70)

with db_cursor() as cur:

    cur.execute(
        "SELECT COUNT(*) as cnt FROM signals WHERE DATE(created_at_ist) = %s",
        (report_date,))
    signal_count = cur.fetchone()["cnt"]

    cur.execute("""
        SELECT COUNT(*) as cnt
        FROM signals
        WHERE DATE(created_at_ist) = %s
          AND signal_status = 'ENTERED'
    """, (report_date,))
    entered_count = cur.fetchone()["cnt"]

    cur.execute("""
        SELECT COUNT(*) as cnt, COALESCE(SUM(pnl),0) as total
        FROM closed_trades
        WHERE DATE(exit_time) = %s
          AND strategy_name != 'orb_nifty'
    """, (report_date,))
    row = dict(cur.fetchone())

    equity_closed = row["cnt"]
    equity_pnl = row["total"]

    cur.execute("""
        SELECT COALESCE(SUM(pnl),0) as total
        FROM closed_trades
        WHERE DATE(exit_time) = %s
          AND strategy_name = 'orb_nifty'
    """, (report_date,))
    orb_pnl = cur.fetchone()["total"]

total_pnl = equity_pnl + orb_pnl

print(f"Equity signals generated : {signal_count}")
print(f"Equity signals entered   : {entered_count}")
print(f"Equity trades closed     : {equity_closed}")
print(f"Equity PnL               : ₹{equity_pnl:,.2f}")
print(f"ORB Nifty PnL            : ₹{orb_pnl:,.2f}")
print("─" * 40)
print(f"Total PnL (all)          : ₹{total_pnl:,.2f}")
print("=" * 70)
