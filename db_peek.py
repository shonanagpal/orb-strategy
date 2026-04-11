import os
import psycopg2
from psycopg2.extras import RealDictCursor

def peek():
    try:
        # Using your existing environment variables
        conn = psycopg2.connect(
            host=os.getenv("PG_HOST"),
            user=os.getenv("PG_USER"),
            password=os.getenv("PG_PASSWORD"),
            dbname=os.getenv("PG_DATABASE")
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # 1. Check total count
        cur.execute("SELECT count(*) FROM signals;")
        total = cur.fetchone()['count']
        
        print(f"\n🔍 DATABASE AUDIT")
        print(f"========================================")
        print(f"Total signals in table: {total}")
        
        if total > 0:
            # 2. Check the breakdown by date to see where they are "hidden"
            print(f"\nBreakdown by Date:")
            cur.execute("SELECT DATE(signal_time_ist) as d, count(*) FROM signals GROUP BY 1 ORDER BY 1 DESC;")
            for row in cur.fetchall():
                print(f" -> {row['d']}: {row['count']} signals")

            # 3. Show the actual data for the most recent 3 signals
            print(f"\nMost Recent Samples:")
            cur.execute("""
                SELECT symbol, signal_time_ist, entry_price_proxy 
                FROM signals 
                ORDER BY created_at_ist DESC LIMIT 3;
            """)
            for row in cur.fetchall():
                print(f" -> {row['symbol']} | {row['signal_time_ist']} | Price: {row['entry_price_proxy']}")
        else:
            print("\n⚠️  The table is empty. Your Mock Generator might not have saved correctly.")

        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Could not connect: {e}")

if __name__ == "__main__":
    peek()
