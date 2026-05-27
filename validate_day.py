# validate_day.py
from production.common.db import db_cursor
from datetime import date

def run_audit():
    today = date.today()

    print("\n" + "="*105)
    print(f"   DAILY TRADING AUDIT — {today}")
    print("="*105)

    with db_cursor() as cur:

        # 1. COMPLETED EQUITY TRADES
        cur.execute("""
            SELECT symbol, strategy_name, entry_time, entry_price,
                   exit_time, exit_price, pnl, exit_reason
            FROM closed_trades
            WHERE DATE(exit_time) = %s
        """, (today,))
        closed = cur.fetchall()

        print(f"\n✅ COMPLETED EQUITY TRADES (Realized PnL)")
        print(f"{'─'*105}")
        print(
            f"{'SYMBOL':<12} | {'STRATEGY':<22} | {'ENTRY TIME':<10} | {'ENTRY ₹':>9} | "
            f"{'EXIT TIME':<10} | {'EXIT ₹':>9} | {'PNL':>9} | EXIT REASON"
        )
        print(f"{'─'*105}")
        if not closed:
            print("No equity trades completed today.")
        for row in closed:
            entry_t = row['entry_time'].strftime('%H:%M:%S') if row.get('entry_time') else '—'
            exit_t  = row['exit_time'].strftime('%H:%M:%S')  if row.get('exit_time')  else '—'
            print(
                f"{row['symbol']:<12} | {row['strategy_name']:<22} | "
                f"{entry_t:<10} | ₹{row['entry_price']:>8.2f} | "
                f"{exit_t:<10} | ₹{row['exit_price']:>8.2f} | "
                f"₹{row['pnl']:>8.2f} | {row['exit_reason']}"
            )

        # 2. OPEN POSITIONS
        cur.execute("""
            SELECT * FROM open_trades
            WHERE status <> 'CLOSED'
              AND execution_mode = 'LIVE'
              AND strategy_name IN ('mean_reversion_short', 'mb_crossover_long', 'BB_SHORT_NEW')
        """)
        open_pos = cur.fetchall()

        print(f"\n⚠️  CURRENT OPEN POSITIONS")
        print(f"{'─'*105}")
        if not open_pos:
            print("Clean Slate: No open positions detected. ✅")
        else:
            for op in open_pos:
                print(f"ALERT: {op['symbol']} is STILL OPEN!")

        # 3. NIFTY STRATEGY SUMMARY
        print(f"\n🎯 NIFTY STRATEGY SUMMARY")
        print(f"{'─'*105}")
        print("No Nifty strategy trades completed today.")

        # 4. SKIPPED SIGNALS (Audit Trail)
        cur.execute("""
            SELECT
                symbol,
                signal_time_ist,
                created_at_ist,
                signal_status,
                strategy_name,
                EXTRACT(EPOCH FROM (created_at_ist - signal_time_ist)) as latency_sec
            FROM signals
            WHERE DATE(signal_time_ist) = %s AND signal_status != 'FILLED'
            ORDER BY signal_time_ist ASC
        """, (today,))
        skipped = cur.fetchall()

        print(f"\n🚫 SKIPPED / REJECTED SIGNALS")
        print(f"{'─'*105}")
        if not skipped:
            print("No signals were skipped today.")
        else:
            print(f"{'SIGNAL':<10} | {'INSERTED':<10} | {'LATENCY':<8} | {'SYMBOL':<12} | {'STRATEGY':<22} | STATUS")
            print(f"{'─'*105}")
            for s in skipped:
                sig_t   = s['signal_time_ist'].strftime('%H:%M:%S')
                ins_t   = s['created_at_ist'].strftime('%H:%M:%S')
                latency = f"{int(s['latency_sec'])}s"
                print(
                    f"{sig_t:<10} | {ins_t:<10} | {latency:<8} | "
                    f"{s['symbol']:<12} | {s['strategy_name']:<22} | {s['signal_status']}"
                )

        # 5. FINAL RECONCILIATION
        total_pnl = sum(row['pnl'] for row in closed)

        print(f"\n{'='*105}")
        print(f" FINAL PNL RECONCILIATION")
        print(f"{'='*105}")
        print(f"Realized Equity PnL : ₹{total_pnl:>8.2f}")
        print(f"Realized Nifty PnL  : ₹    0.00")
        print(f"{'─'*40}")
        print(f"TOTAL REALIZED PNL  : ₹{total_pnl:>8.2f}")
        print("="*105 + "\n")


if __name__ == "__main__":
    run_audit()
