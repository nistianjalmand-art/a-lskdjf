import sys, os
from datetime import datetime, timezone
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analysis.engine import StructureEngine

def test_snap_timezone():
    engine = StructureEngine()
    
    # Simuliere Broker: UTC+3 (H4 Kerze startet um 21:00 UTC)
    # Unix 1000000000 ist Sonntag, 9. September 2001, 01:46:40 UTC
    # Nehmen wir ein einfacheres Beispiel:
    # 00:00 UTC = 0
    # 21:00 UTC = 21 * 3600 = 75600
    
    # H4 Pivot kommt vom Broker: Startet um 21:00 UTC
    p_h4 = {"time": 75600, "is_high": True, "price": 100.0}
    
    # H1 Kerzen (fine_candles) für den Bereich
    # Angenommen das echte High war bei 00:30 UTC des FOLGETAGES.
    # Also 27.5 Stunden nach 00:00, oder 3.5 Stunden nach dem H4 Start (21:00).
    # 21:00 + 3.5h = 00:30 UTC = 75600 + 12600 = 88200
    
    class MockCandle:
        def __init__(self, t, h):
            self.time = datetime.fromtimestamp(t, tz=timezone.utc)
            self.high = h
            self.low = h - 1
            
    # Wir erstellen H1 Kerzen von 20:00 bis 02:00 UTC
    candles = [
        MockCandle(72000, 90), # 20:00 (Vorherige H4 Kerze nach UTC-Logik)
        MockCandle(75600, 95), # 21:00 (H4 Start Broker)
        MockCandle(79200, 96), # 22:00
        MockCandle(82800, 97), # 23:00
        MockCandle(86400, 98), # 00:00
        MockCandle(88200, 110),# 00:30 (DAS ECHTE HIGH!)
        MockCandle(90000, 99), # 01:00
    ]
    
    # ALTE LOGIK (UTC-Gitter):
    # window_size = 14400 (4h)
    # anchor_ts = (75600 // 14400) * 14400 = (5.25) * 14400 = 5 * 14400 = 72000
    # window = [72000, 86400] (20:00 UTC bis 00:00 UTC)
    # ERGEBNIS: Das High bei 88200 wird NICHT GEFUNDEN!
    # Er würde stattdessen die 00:00 Kerze (86400) als Ende nehmen oder das High bei 23:00.
    
    # NEUE LOGIK (Broker-Anker):
    # anchor_ts = 75600
    # window = [75600, 89100] (21:00 UTC bis 01:00 UTC)
    # ERGEBNIS: Das High bei 88200 wird gefunden!

    snapped = engine._snap_pivots([p_h4], candles, "4h")
    
    print(f"Original Time: {p_h4['time']} (21:00 UTC)")
    print(f"Snapped Time:  {snapped[0]['time']} ({datetime.fromtimestamp(snapped[0]['time'], tz=timezone.utc)})")
    
    assert snapped[0]['time'] == 88200, f"Expected 88200 (00:30), but got {snapped[0]['time']}"
    print("SUCCESS: Snapping is now broker-timezone-aware!")

if __name__ == "__main__":
    test_snap_timezone()
