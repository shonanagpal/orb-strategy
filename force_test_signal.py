import os
import psycopg2

def force_test_signal_final():
    try:
        conn = psycopg2.connect(
            host=os.getenv("PG_HOST"),
            user=os.getenv("PG_USER"),
            password=os.getenv("PG_PASSWORD"),
            dbname=os.getenv("PG_DATABASE")
        )
        cur = conn.cursor()
        
        query = """
            INSERT INTO signals (
                strategy_name, 
                strategy_version, 
                timeframe,
                symbol, 
                side,
                signal_time_ist, 
                entry_price_proxy, 
                signal_status
            )
            VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s);
        """
        # Adding 'timeframe' to satisfy the constraint
        data = ('Bollinger_V2', '1.0', '15m', 'TEST_OK', 'BUY', 100.50, 'MOCK_TEST')
        
        cur.execute(query, data)
        conn.commit() 
        
        print("✅ SUCCESS! The signal finally landed in the database.")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Still hitting a constraint: {e}")

if __name__ == "__main__":
    force_test_signal_final()
