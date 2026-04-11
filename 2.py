import os
import psycopg2
from psycopg2.extras import RealDictCursor

def check_signals():
    try:
        conn = psycopg2.connect(
            host=os.getenv("PG_HOST"),
            user=os.getenv("PG_USER"),
            password=os.getenv("PG_PASSWORD"),
            dbname=os.getenv("PG_DATABASE")
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Checking for any signals regardless of date
        cur.execute("SELECT count(*) FROM signals;")
        total_count = cur.fetchone()['count']
        
        print(f"📊 DATABASE STATUS REPORT")
        print(f"{'─'*30}")
        print(f"Total Signals Found: {total_count}")
        
        if total_count > 0:
            # Check the most recent 5 signals
            cur.execute("""
                SELECT symbol, signal_time_ist, entry_price_proxy, signal_status 
                FROM signals 
                ORDER BY created_at_ist DESC 
                LIMIT 5;
            """)
            rows = cur.fetchall()
            print("\nLatest Signals in DB:")
            print(f"{'SYMBOL':<12} | {'TIME (IST)':<15} | {'PRICE':<8} | {'STATUS'}")
            print(f"{'─'*55}")
            for row in rows:
                price = f"₹{row['entry_price_proxy']:.2f}" if row['entry_price_proxy'] else "NULL"
                time_str = row['signal_time_ist'].strftime('%Y-%m-%d %H:%M')
                print(f"{row['symbol']:<12} | {time_str:<15} | {price:<8} | {row['signal_status']}")
        else:
            print("✨ The table is perfectly empty and ready for a fresh run.")

        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Connection Error: {e}")

if __name__ == "__main__":
    check_signals()
