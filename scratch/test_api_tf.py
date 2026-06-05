import requests
import time

def test_tf():
    base_url = "http://localhost:8000/api/history"
    symbol = "XAUUSD"
    
    for tf in ["1m", "5m", "1h"]:
        print(f"Testing timeframe: {tf}")
        resp = requests.get(f"{base_url}?symbol={symbol}&timeframe={tf}&count=5")
        if resp.status_code == 200:
            data = resp.json()
            candles = data.get("candles", [])
            if candles:
                print(f"  First candle time: {candles[0]['time']}")
                print(f"  Last candle time:  {candles[-1]['time']}")
                diff = candles[1]['time'] - candles[0]['time'] if len(candles) > 1 else 0
                print(f"  Time diff: {diff} seconds")
            else:
                print("  No candles returned")
        else:
            print(f"  Error: {resp.status_code}")
        print("-" * 20)

if __name__ == "__main__":
    test_tf()
