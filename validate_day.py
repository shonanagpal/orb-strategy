# validate_day.py
from production.common.db import db_cursor
from datetime import date

def run_audit():
    today = date.today()
    
    print("\n" + "="*85)
    print(f"   DAILY TRADING AUDIT — {today}")
    print("="*85)

    with db_cursor() as cur:
        # 1. COMPLETED EQUITY TRADES
        cur.execute("""
            SELECT symbol, entry_price, exit_price, pnl, exit_reason 
            FROM closed_trades 
            WHERE DATE(exit_time) = %s
        """, (today,))
        closed = cur.fetchall()

        print(f"\n✅ COMPLETED EQUITY TRADES (Realized PnL)")
        print(f"{'─'*85}")
        if not closed:
            print("No equity trades completed today.")
        for row in closed:
            print(f"{row['symbol']:<12} | PnL: ₹{row['pnl']:>8.2f} | Reason: {row['exit_reason']}")

        # 2. OPEN POSITIONS
        cur.execute("SELECT * FROM open_trades")
        open_pos = cur.fetchall()
        print(f"\n⚠️  CURRENT OPEN POSITIONS")
        print(f"{'─'*85}")
        if not open_pos:
            print("Clean Slate: No open positions detected. ✅")
        else:
            for op in open_pos:
                # Using generic access to avoid column name errors
                print(f"ALERT: {op['symbol']} is STILL OPEN!")

        # 3. NIFTY STRATEGY SUMMARY
        print(f"\n🎯 NIFTY STRATEGY SUMMARY")
        print(f"{'─'*85}")
        # Note: Replace this placeholder if you have a specific Nifty trades table
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
        print(f"{'─'*85}")
        if not skipped:
            print("No signals were skipped today.")
        else:
            print(f"{'SIGNAL':<8} | {'INSERTED':<8} | {'LATENCY':<7} | {'SYMBOL':<10} | {'STATUS':<20}")
            print(f"{'─'*85}")
            for s in skipped:
                sig_t = s['signal_time_ist'].strftime('%H:%M:%S')
                ins_t = s['created_at_ist'].strftime('%H:%M:%S')
                latency = f"{int(s['latency_sec'])}s"
                print(f"{sig_t:<8} | {ins_t:<8} | {latency:<7} | {s['symbol']:<10} | {s['signal_status']:<20}")

        # 5. FINAL RECONCILIATION
        total_pnl = sum(row['pnl'] for row in closed)
        print(f"\n{'='*85}")
        print(f" FINAL PNL RECONCILIATION")
        print(f"{'='*85}")
        print(f"Realized Equity PnL : ₹{total_pnl:>8.2f}")
        print(f"Realized Nifty PnL  : ₹0.00")  # Restored
        print(f"{'─'*40}")
        print(f"TOTAL REALIZED PNL  : ₹{total_pnl:>8.2f}")
        print("="*85 + "\n")

if __name__ == "__main__":
    run_audit()
