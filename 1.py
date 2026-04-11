import os
import psycopg2

def purge_all_signals():
    try:
        conn = psycopg2.connect(
            host=os.getenv("PG_HOST"),
            user=os.getenv("PG_USER"),
            password=os.getenv("PG_PASSWORD"),
            dbname=os.getenv("PG_DATABASE")
        )
        cur = conn.cursor()
        
        # 1. Clear signals (the parent table)
        cur.execute("TRUNCATE TABLE signals CASCADE;")
        
        # 2. Clear open_trades (the active tracker)
        cur.execute("TRUNCATE TABLE open_trades CASCADE;")
        
        conn.commit()
        print("✅ Database Purged: signals and open_trades are now empty.")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Purge failed: {e}")

if __name__ == "__main__":
    purge_all_signals()
