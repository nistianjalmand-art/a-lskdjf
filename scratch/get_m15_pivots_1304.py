import asyncio
import os
import sys
from datetime import datetime, timezone

# Project root for imports
sys.path.insert(0, os.path.abspath(os.curdir))

from metaapi_client import metaapi
from analysis.engine import StructureEngine
from analysis.models import dicts_to_candles

async def main():
    print("Initializing MT5 Connection...")
    try:
        await metaapi.initialize()
    except Exception as e:
        print(f"Error: {e}")
        return

    symbol = "XAUUSD" # Default symbol from config
    timeframe = "15m"
    
    # Range for April 13th, 2026
    start_dt = datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc)
    end_dt   = datetime(2026, 4, 13, 23, 59, tzinfo=timezone.utc)
    
    print(f"Fetching M15 candles for {symbol} on 13.04.2026...")
    candles_raw = await metaapi.get_historical_candles_range(symbol, timeframe, start_dt, end_dt)
    
    if not candles_raw:
        print("No candles found for this range.")
        await metaapi.shutdown()
        return

    candles = dicts_to_candles(candles_raw)
    engine = StructureEngine(pivot_length=2)

    # 1. Micropivots (Lila Logic)
    print("\n--- MICROPIVOTS (Lila) ---")
    micro_pivots = engine.compute_pivots_for_candles(timeframe, candles, tf_label="15m")
    for p in micro_pivots:
        p_type = "HIGH" if p.is_high else "LOW"
        print(f"{p.time.strftime('%H:%M')} | {p_type:4s} | Price: {p.price:.2f}")

    # 2. Blaue Struktur (M15 Inner Structure / Aqua)
    # The Inner Structure is usually nested within H1 or H4. 
    # For a direct list, we use the smart structure logic.
    print("\n--- BLAUE STRUKTUR (M15 Inner / Aqua) ---")
    # We need H1 segments as parents if we follow the nesting logic.
    # But if the user just wants the "Structure" on M15, we can use compute_master_structure
    from analysis.structure import filter_alternating_pivots, compute_master_structure
    
    # Filter for alternating pivots (Lila Logic)
    alternating = filter_alternating_pivots(micro_pivots)
    
    # Compute the "Master" structure on M15 TF
    level1, inner, temp = compute_master_structure(alternating)
    
    combined = level1 + temp
    combined.sort(key=lambda x: x.time)
    
    for p in combined:
        p_type = "HIGH" if p.is_high else "LOW"
        print(f"{p.time.strftime('%H:%M')} | {p_type:4s} | Price: {p.price:.2f}")

    await metaapi.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
