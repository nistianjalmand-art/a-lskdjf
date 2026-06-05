import sys
import os
import asyncio
from datetime import datetime, timezone

# Pfad-Hacking damit die imports funktionieren
sys.path.append(os.getcwd())

from analysis.engine import StructureEngine
from metaapi_client import metaapi
from analysis.structure import filter_alternating_pivots, enforce_strict_trend

def smart_filter_v3(pivots: list, is_bullish: bool) -> list:
    """
    Sammelt alle wichtigen Punkte für die Struktur:
    - HHs und HLs (Rausch-Filterung)
    - ABER: Erhält Anomalien (LL im Uptrend / HH im Downtrend)
    - Damit die iterative Rollback-Logik (enforce_strict_trend) triggern kann.
    """
    if len(pivots) < 2: return pivots
    
    result = [pivots[0]] 
    searching_high = not pivots[0].is_high
    last_h = pivots[0] if pivots[0].is_high else None
    last_l = pivots[0] if not pivots[0].is_high else None
    best_candidate = None
    
    epsilon = 0.0001 # Toleranz

    for p in pivots[1:]:
        if is_bullish:
            if searching_high:
                if p.is_high:
                    if best_candidate is None or p.price > best_candidate.price:
                        best_candidate = p
                else: # p.is_low
                    if last_l and p.price < (last_l.price - epsilon):
                        if best_candidate: result.append(best_candidate)
                        result.append(p)
                        last_l = p
                        best_candidate = None
                        searching_high = True
                        continue
                    if best_candidate:
                        result.append(best_candidate)
                        last_h = best_candidate
                        searching_high = False
                        best_candidate = p
            else: # searching_low (HL)
                if not p.is_high:
                    if last_l and p.price < (last_l.price - epsilon):
                        result.append(p)
                        last_l = p
                        best_candidate = None
                        searching_high = True
                        continue
                    if best_candidate is None or p.price < best_candidate.price:
                        best_candidate = p
                else: # p.is_high
                    if last_h and p.price > (last_h.price + epsilon):
                        if best_candidate: result.append(best_candidate)
                        best_candidate = p
                        searching_high = True
        else: # Bearish (Downtrend)
            if not searching_high: # searching_low (LL)
                if not p.is_high:
                    if best_candidate is None or p.price < best_candidate.price:
                        best_candidate = p
                else: # p.is_high (Anomalie HH?)
                    if last_h and p.price > (last_h.price + epsilon):
                        if best_candidate: result.append(best_candidate)
                        result.append(p)
                        last_h = p
                        best_candidate = None
                        searching_high = False
                        continue
                    if best_candidate:
                        result.append(best_candidate)
                        last_l = best_candidate
                        searching_high = True
                        best_candidate = p
            else: # searching_high (LH)
                if p.is_high:
                    if last_h and p.price > (last_h.price + epsilon):
                        result.append(p)
                        last_h = p
                        best_candidate = None
                        searching_high = False
                        continue
                    if best_candidate is None or p.price > best_candidate.price:
                        best_candidate = p
                else: # p.is_low
                    if last_l and p.price < (last_l.price - epsilon):
                        if best_candidate: result.append(best_candidate)
                        best_candidate = p
                        searching_high = False

    if best_candidate and best_candidate not in result: result.append(best_candidate)
    if pivots[-1] not in result: result.append(pivots[-1])
    return filter_alternating_pivots(result)

async def test_day(symbol, date_str):
    y, m, d = map(int, date_str.split("-"))
    dt_start = datetime(y, m, d, 0, 0, tzinfo=timezone.utc)
    dt_end   = datetime(y, m, d, 23, 59, tzinfo=timezone.utc)

    candles_raw = await metaapi.get_historical_candles_range(symbol, "15m", dt_start, dt_end)
    if not candles_raw: 
        print(f"Keine Daten für {date_str}")
        return
    
    from analysis.models import dicts_to_candles
    candles = dicts_to_candles(candles_raw)
    engine = StructureEngine()
    lila = engine.compute_pivots_for_candles("15m", candles, "15m", override_length=1)
    alternating = filter_alternating_pivots(lila)
    
    # Einfache Trend-Bestimmung
    is_bullish = candles[-1].close > candles[0].open
    
    print(f"\n--- TEST {date_str} (Trend: {'UP' if is_bullish else 'DOWN'}) ---")
    filtered = smart_filter_v3(alternating, is_bullish)
    final = enforce_strict_trend(filtered, is_bullish)
    
    print(f"Result: {len(final)} Aqua-Pivots.")
    if len(final) > 0:
        print(f"Letzter Punkt: {final[-1].time.strftime('%H:%M')} | Price: {final[-1].price:.2f}")

async def test_all():
    try:
        await metaapi.initialize()
    except Exception as e:
        print(f"Error: {e}")
        return

    symbol = "XAUUSD"
    days = ["2026-04-13", "2026-04-14", "2026-04-15", "2026-04-16", "2026-04-17"]
    for day in days:
        await test_day(symbol, day)

if __name__ == "__main__":
    asyncio.run(test_all())
