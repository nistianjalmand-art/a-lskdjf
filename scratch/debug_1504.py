import sys
import os
import asyncio
from datetime import datetime, timezone

sys.path.append(os.getcwd())

from analysis.engine import StructureEngine
from metaapi_client import metaapi
from analysis.structure import filter_alternating_pivots, enforce_strict_trend, _filter_trend_only

async def debug():
    await metaapi.initialize()
    engine = StructureEngine()
    
    # 15.04.2026
    start = datetime(2026, 4, 15, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 15, 23, 59, tzinfo=timezone.utc)
    
    candles_raw = await metaapi.get_historical_candles_range("XAUUSD", "15m", start, end)
    from analysis.models import dicts_to_candles
    candles = dicts_to_candles(candles_raw)
    
    lila = engine.compute_pivots_for_candles("15m", candles, "15m", override_length=1)
    alternating = filter_alternating_pivots(lila)
    
    print(f"--- LILA PIVOTS 15.04. ---")
    for p in alternating:
        if "05:00" <= p.time.strftime("%H:%M") <= "15:00":
             print(f"{p.time.strftime('%H:%M')} | {'HIGH' if p.is_high else 'LOW':4} | {p.price:.2f}")

    is_bullish = False # Vermutlich Downtrend an dem Tag (Gold fiel)
    
    print(f"\n--- SMART FILTER TEST (Bearish) ---")
    filtered = _filter_trend_only(alternating, is_bullish)
    for p in filtered:
        if "05:00" <= p.time.strftime("%H:%M") <= "15:00":
             print(f"{p.time.strftime('%H:%M')} | {'HIGH' if p.is_high else 'LOW':4} | {p.price:.2f}")

    print(f"\n--- CLEANUP TEST (Bearish) ---")
    final = enforce_strict_trend(filtered, is_bullish)
    for p in final:
        if "05:00" <= p.time.strftime("%H:%M") <= "15:00":
             print(f"{p.time.strftime('%H:%M')} | {'HIGH' if p.is_high else 'LOW':4} | {p.price:.2f}")

if __name__ == "__main__":
    asyncio.run(debug())
