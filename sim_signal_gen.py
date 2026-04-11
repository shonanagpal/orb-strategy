import os
import sys
import pandas as pd
import glob

# Ensure script can see your production logic
sys.path.append(os.getcwd())
# Replace 'your_strategy_file' with the actual name of your strategy module
# from production.signal_generator.strategy import check_signals 

# --- CONFIG ---
PARQUET_DIR = os.path.expanduser("~/trading/Bollinger/assembled_candles")
SIM_CAPITAL = 200000 

def run_signal_sim():
    print(f"🔍 SIGNAL GENERATION SIMULATION")
    print(f"📂 Source: {PARQUET_DIR}\n")
    
    parquet_files = glob.glob(f"{PARQUET_DIR}/*.parquet")
    
    if not parquet_files:
        print("❌ No parquet files found.")
        return

    print(f"{'SYMBOL':<12} | {'LAST CLOSE':<10} | {'SIGNAL':<6} | {'PROXY PRICE':<12} | {'EST. QTY'}")
    print("-" * 75)

    for file_path in parquet_files:
        symbol = os.path.basename(file_path).replace(".parquet", "")
        
        # Load the data
        df = pd.read_parquet(file_path)
        
        if df.empty:
            continue

        # --- REPLICATE YOUR STRATEGY LOGIC HERE ---
        # This is a placeholder for your specific Bollinger check
        # latest_candle = df.iloc[-1]
        
        # Example Logic (Replace with your actual import or logic):
        # is_signal, entry_price = check_signals(df) 
        
        # For this test, let's assume we found a signal to verify the handshake
        last_price = df['close'].iloc[-1]
        
        # We simulate what the 'postgres_writer' would receive
        mock_signal = {
            "symbol": symbol,
            "entry_price_proxy": last_price,
            "side": "BUY"
        }

        # Calculate expected QTY
        qty = int(SIM_CAPITAL / mock_signal['entry_price_proxy'])
        
        print(f"{symbol:<12} | {last_price:<10.2f} | {'BUY':<6} | {mock_signal['entry_price_proxy']:<12.2f} | {qty}")

if __name__ == "__main__":
    run_signal_sim()
