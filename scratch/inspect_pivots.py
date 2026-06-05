import sys
import os
import asyncio
from datetime import datetime, timezone

# Pfad-Hacking damit die imports funktionieren
# Wir gehen davon aus, dass das Skript im Unterordner 'scratch' liegt
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from analysis.engine import StructureEngine
from metaapi_client import metaapi

async def inspect():
    print("Versuche MT5 zu initialisieren...")
    # Wir initialisieren den MT5 Client (wirft Exception bei Fehler)
    try:
        await metaapi.initialize()
    except Exception as e:
        print(f"Fehler: Konnte MT5 nicht initialisieren: {e}")
        print("Bitte öffne dein MetaTrader 5 Terminal und stelle sicher, dass Algo-Trading aktiviert ist.")
        return

    engine = StructureEngine()
    symbol = "XAUUSD" # Wie gewünscht XAUUSD
    
    # Zeitraum für den 13.04.2026 (letzte Woche)
    start = int(datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc).timestamp())
    end = int(datetime(2026, 4, 13, 23, 59, tzinfo=timezone.utc).timestamp())

    print(f"Lese Daten für {symbol} am 13.04.2026 aus...\n")
    
    # Wir nutzen pivot_length 1 (entspricht Reglerstellung 1)
    data = await engine.get_smart_structure(symbol, "15m", start, end, 1000, 1)
    
    day_start = start
    day_end   = end

    def is_on_day(p):
        t = p.get('time', 0)
        return day_start <= t <= day_end

    # 2. Rohkerzen um 15:00 prüfen
    print("\n--- M15 KERZEN UM 15:00 (CHECK) ---")
    check_start = int(datetime(2026, 4, 13, 14, 0, tzinfo=timezone.utc).timestamp())
    check_end   = int(datetime(2026, 4, 13, 16, 0, tzinfo=timezone.utc).timestamp())
    for c in data.get('micro_pivots', []): # This field in 'data' is actually the processed pivots, not candles.
        pass
    
    # Wir laden die Kerzen manuell für den Check
    from analysis.models import dicts_to_candles
    candles_raw = await metaapi.get_historical_candles_range(symbol, "15m", 
                                                            datetime(2026, 4, 13, 14, 0, tzinfo=timezone.utc),
                                                            datetime(2026, 4, 13, 16, 0, tzinfo=timezone.utc))
    candles = dicts_to_candles(candles_raw)
    for c in candles:
        print(f"Candle: {c.time} | H: {c.high:.2f} | L: {c.low:.2f} | C: {c.close:.2f}")

    # 3. Ausbruch der M15 Mikropivots (Lila)
    print("\n--- M15 MICRO-PIVOTS (LILA) ---")
    local_pivots = data.get('micro_pivots', [])
    count_lila = 0
    for p in local_pivots:
        if is_on_day(p):
            print(f"{p.get('time_iso')} | {'HIGH' if p['is_high'] else 'LOW':4} | {p['price']:.5f}")
            count_lila += 1
    if count_lila == 0: print("Keine Lila Pivots für diesen Tag gefunden.")

    # 3. Ausbruch der H1 Inner-Struktur (Grün)
    print("\n--- H1 GRÜNE STRUKTUR (INNER) ---")
    h1_inner_data = data.get('h1_inner_structure', [])
    count_h1 = 0
    if h1_inner_data and len(h1_inner_data) > 0:
        for p in h1_inner_data[0]:
            if is_on_day(p):
                print(f"{p.get('time_iso')} | {'HIGH' if p['is_high'] else 'LOW':4} | {p['price']:.5f}")
                count_h1 += 1
    if count_h1 == 0: print("Keine H1-Struktur-Pivots für diesen Tag gefunden.")

    # 4. Ausbruch der M15 Aqua-Struktur (Inner)
    print("\n--- M15 AQUA-STRUKTUR (INNER) ---")
    m15_inner_data = data.get('m15_inner_structure', [])
    count_aqua = 0
    if m15_inner_data and len(m15_inner_data) > 0:
        for p in m15_inner_data[0]:
            if is_on_day(p):
                print(f"{p.get('time_iso')} | {'HIGH' if p['is_high'] else 'LOW':4} | {p['price']:.5f}")
                count_aqua += 1
    if count_aqua == 0: print("Keine Aqua-Struktur-Pivots für diesen Tag gefunden.")

    print("\nInspektion abgeschlossen.")

if __name__ == "__main__":
    asyncio.run(inspect())
