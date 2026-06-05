"""
Test: compute_level1_from_micro_pivots() – Inner Structure Bug-Analyse
Prüft Timestamp-Ordnung, Duplikate und den Replay-Mechanismus.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timezone
from analysis.models import PivotPoint
from analysis.structure import compute_master_structure


def make_pivot(time_sec, price, is_high):
    return PivotPoint(
        time=datetime.fromtimestamp(time_sec, tz=timezone.utc),
        price=price,
        is_high=is_high,
    )


# ---------------------------------------------------------------------------
# Sequenz 1 – einfacher LONG-Trend (aus der Aufgabenstellung)
# ---------------------------------------------------------------------------
print("=" * 60)
print("SEQUENZ 1: Einfacher LONG-Trend")
print("=" * 60)

pivots_1 = [
    make_pivot(1000, 100, True),   # H0 – init → start SHORT
    make_pivot(1100,  80, False),  # L1 – erstes LL im SHORT
    make_pivot(1200,  90, True),   # H1 – LH Korrektur
    make_pivot(1300,  75, False),  # L2 – neues LL
    make_pivot(1400,  85, True),   # H2 – Korrektur-High (kommt in correction_buffer)
    make_pivot(1500,  70, False),  # L3 – neues LL
    make_pivot(1600, 105, True),   # H3 – BOS UP (> H0=100) → Replay von [L3, H2, L2... wait, correction_buffer has H2 only]
    make_pivot(1700,  95, False),  # L4 – HL im LONG
    make_pivot(1800, 115, True),   # H4 – HH im LONG
    make_pivot(1900, 105, False),  # L5 – HL im LONG
    make_pivot(2000, 125, True),   # H5 – neues HH
]

level1, inner, temp = compute_master_structure(pivots_1)

print("\n=== LEVEL1 ===")
for p in level1:
    print(f"  t={int(p.time.timestamp())}  {'HIGH' if p.is_high else 'LOW ':4s}  @ {p.price}")

print("\n=== INNER STRUCTURE ===")
for i, p in enumerate(inner):
    print(f"  [{i}]  t={int(p.time.timestamp())}  {'HIGH' if p.is_high else 'LOW ':4s}  @ {p.price}")

print("\n=== TEMP STRUCTURE ===")
for p in temp:
    print(f"  t={int(p.time.timestamp())}  {'HIGH' if p.is_high else 'LOW ':4s}  @ {p.price}")

# ── Timestamp-Ordnung prüfen (wie Lightweight Charts es erwartet) ──────────
print("\n=== TIMESTAMPS CHECK (inner_structure, raw) ===")
times_raw = [int(p.time.timestamp()) for p in inner]
print("Raw times:", times_raw)
ok = True
for i in range(1, len(times_raw)):
    if times_raw[i] <= times_raw[i - 1]:
        print(f"  PROBLEM: t[{i}]={times_raw[i]} <= t[{i-1}]={times_raw[i-1]}")
        ok = False
if ok:
    print("  Alle Timestamps strikt aufsteigend – OK")

# ── Simuliere Frontend-Filter (Sort + Dedup) ───────────────────────────────
print("\n=== NACH FRONTEND-FILTER (sort + dedup) ===")
inner_dicts = [{"time": int(p.time.timestamp()), "price": p.price} for p in inner]
# Sort: erst nach time, Tiebreak nach price
sorted_inner = sorted(inner_dicts, key=lambda x: (x["time"], x["price"]))
# Dedup: gleiche Zeit UND gleicher Preis entfernen
filtered = [
    p for i, p in enumerate(sorted_inner)
    if i == 0
    or p["time"] != sorted_inner[i - 1]["time"]
    or p["price"] != sorted_inner[i - 1]["price"]
]
print(f"  Vor Filter: {len(inner_dicts)} Punkte  ->  Nach Filter: {len(filtered)} Punkte")
for i, p in enumerate(filtered):
    print(f"  [{i}]  t={p['time']}  price={p['price']}")

# ── Prüfe: gibt es nach dem Sort noch gleiche Timestamps? ─────────────────
print("\n=== DUPLICATE TIMESTAMPS NACH SORT ===")
dup_found = False
for i in range(1, len(filtered)):
    if filtered[i]["time"] == filtered[i - 1]["time"]:
        print(f"  DUPLICATE TIME: [{i-1}] t={filtered[i-1]['time']} price={filtered[i-1]['price']}"
              f"  UND  [{i}] t={filtered[i]['time']} price={filtered[i]['price']}")
        dup_found = True
if not dup_found:
    print("  Keine doppelten Timestamps – OK")

# ── Prüfe: sind nach Sort+Dedup die Timestamps STRIKT aufsteigend? ────────
print("\n=== STRICT ORDER NACH FILTER ===")
strict_ok = True
for i in range(1, len(filtered)):
    if filtered[i]["time"] <= filtered[i - 1]["time"]:
        print(f"  FEHLER: [{i}] t={filtered[i]['time']} <= [{i-1}] t={filtered[i-1]['time']}")
        strict_ok = False
if strict_ok:
    print("  Strikt aufsteigend – OK für Lightweight Charts")

# ---------------------------------------------------------------------------
# Sequenz 2 – Testet genau den Replay-Mechanismus beim BOS
# Prüft ob correction_buffer-Punkte FRÜHERE Timestamps als impulse_start haben
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("SEQUENZ 2: Replay-Bug Test")
print("  (correction_buffer: H1=t1200 landet als erstes HH in LONG,")
print("   aber impulse_start=L3=t1500 -> H1.time < impulse_start.time!)")
print("=" * 60)

# Hier ist die kritische Sequenz:
# SHORT trend: init=H0(t=100), LL=L1(t=200), dann correction_buffer=[H1(t=300)]
# BOS UP: H2(t=400) > H0=100 → impulse_start = L1 (letztes LL)
# Replay: H1 (t=300) aus correction_buffer wird als erstes HH verarbeitet
# Frage: inner_structure bekommt (impulse_start=L1 @ t=200, H1 @ t=300) → t=200 < t=300 → OK?
# ABER: correction_buffer enthält manchmal nur Lows, nicht das Hoch selbst!

pivots_2 = [
    make_pivot(1000, 100, True),   # H0 – init SHORT, bos_level=100
    make_pivot(1100,  80, False),  # L1 – LL: inner=(H0@100, L1@80), last_conf=L1, bos_level=100
    make_pivot(1200,  90, True),   # H1 – correction High → highest_since_ll=H1, buf=[H1]
    make_pivot(1300,  75, False),  # L2 – neues LL: inner.append(highest_since_ll=H1, L2), buf=[], last_conf=L2
    make_pivot(1400,  85, True),   # H2 – correction High → highest_since_ll=H2, buf=[H2]
    make_pivot(1500,  65, False),  # L3 – neues LL: inner.append(H2, L3), buf=[], last_conf=L3
    make_pivot(1600,  95, True),   # H3 – correction → buf=[H3], highest_since_ll=H3
    make_pivot(1700,  60, False),  # L4 – neues LL: inner.append(H3, L4), buf=[], last_conf=L4
    make_pivot(1800, 110, True),   # H4 – BOS UP (>100): commit(H0,L4), trend=1, impulse_start=L4
                                   #       Replay: buf=[H3? NEIN buf war leer nach L4]
                                   #       → buf=[] nach LL, also kein Replay!
]

level1_2, inner_2, temp_2 = compute_master_structure(pivots_2)

print("\n=== INNER STRUCTURE (Seq 2) ===")
for i, p in enumerate(inner_2):
    print(f"  [{i}]  t={int(p.time.timestamp())}  {'HIGH' if p.is_high else 'LOW ':4s}  @ {p.price}")

inner_dicts_2 = [{"time": int(p.time.timestamp()), "price": p.price} for p in inner_2]
sorted_2 = sorted(inner_dicts_2, key=lambda x: (x["time"], x["price"]))
filtered_2 = [
    p for i, p in enumerate(sorted_2)
    if i == 0 or p["time"] != sorted_2[i - 1]["time"] or p["price"] != sorted_2[i - 1]["price"]
]
print(f"\nNach Filter: {len(filtered_2)} Punkte")

# ---------------------------------------------------------------------------
# Sequenz 3 – Replay mit correction_buffer der HÖHERE Timestamps als impulse_start hat
# Das ist der kritische Fall: BOS kommt BEVOR ein neues LL das buffer leert
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("SEQUENZ 3: BOS direkt nach Korrektur (buffer nicht geleert)")
print("=" * 60)

# SHORT: H0, L1 (LL), H1 (correction, buf=[H1]), H2 (BOS UP, > H0)
# → correction_buffer = [H1]  (L2 kam NIE, also buf nicht geleert)
# → nach BOS: impulse_start=L1(t=1100), Replay von [H1(t=1200)]
# → H1 wird als erstes HH in LONG verarbeitet
# → inner.append(impulse_start=L1@t=1100, H1@t=1200) → 1100 < 1200 → OK
# ABER: Was wenn buf=[H_früher] durch mehrere Updates?

pivots_3 = [
    make_pivot(1000, 100, True),   # H0
    make_pivot(1100,  80, False),  # L1 – LL, last_conf=L1, buf=[]
    make_pivot(1200,  90, True),   # H1 – correction, buf=[H1], highest_since_ll=H1
    make_pivot(1300, 105, True),   # H2 – BOS UP (>100): impulse_start=L1, Replay=[H1]
                                   # Replay: H1(t=1200) als erstes HH → inner=(L1@1100, H1@1200)
    make_pivot(1400,  95, False),  # L – HL
    make_pivot(1500, 115, True),   # H – neues HH
]

level1_3, inner_3, temp_3 = compute_master_structure(pivots_3)

print("\n=== INNER STRUCTURE (Seq 3) ===")
for i, p in enumerate(inner_3):
    print(f"  [{i}]  t={int(p.time.timestamp())}  {'HIGH' if p.is_high else 'LOW ':4s}  @ {p.price}")

inner_dicts_3 = [{"time": int(p.time.timestamp()), "price": p.price} for p in inner_3]
sorted_3 = sorted(inner_dicts_3, key=lambda x: (x["time"], x["price"]))
filtered_3 = [
    p for i, p in enumerate(sorted_3)
    if i == 0 or p["time"] != sorted_3[i - 1]["time"] or p["price"] != sorted_3[i - 1]["price"]
]
print(f"\nNach Filter: {len(filtered_3)} Punkte")
for i, p in enumerate(filtered_3):
    print(f"  [{i}]  t={p['time']}  price={p['price']}")

print("\n=== STRICT ORDER NACH FILTER (Seq 3) ===")
strict_ok = True
for i in range(1, len(filtered_3)):
    if filtered_3[i]["time"] <= filtered_3[i - 1]["time"]:
        print(f"  FEHLER: [{i}] t={filtered_3[i]['time']} <= [{i-1}] t={filtered_3[i-1]['time']}")
        strict_ok = False
if strict_ok:
    print("  OK")

# ---------------------------------------------------------------------------
# Sequenz 4 – der echte Replay-Bug: correction_buffer enthält FRÜHERE Timestamps
# SHORT → BOS UP → Replay der correction_buffer Items
# Das korrektur-High (H1) wird als erstes HH behandelt,
# ABER: Was wenn correction_buffer aus dem LETZTEN LL-Schritt stammt
# und der BOS_HIGH einen NEUEN correction_buffer aufbaut?
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("SEQUENZ 4: Vollständiger Trend mit Timestamp-Kollision-Check")
print("=" * 60)

# Teste ob impulse_start (ein LL) denselben Timestamp wie ein correction_buffer-Item hat
pivots_4 = [
    make_pivot(1000, 100, True),
    make_pivot(1100,  80, False),   # LL
    make_pivot(1200,  90, True),    # correction H → buf=[H]
    make_pivot(1200,  95, True),    # gleicher Timestamp, höherer Preis → UPDATE highest_since_ll
    make_pivot(1300, 105, True),    # BOS UP > 100
    make_pivot(1400,  98, False),
    make_pivot(1500, 112, True),
]

level1_4, inner_4, temp_4 = compute_master_structure(pivots_4)
print("\n=== INNER STRUCTURE (Seq 4, Timestamp-Kollision) ===")
for i, p in enumerate(inner_4):
    print(f"  [{i}]  t={int(p.time.timestamp())}  {'HIGH' if p.is_high else 'LOW ':4s}  @ {p.price}")

# ---------------------------------------------------------------------------
# Sequenz 5 – Direkt den "zurückgespulten" Correction-Buffer tracen
# Wir stellen sicher dass nach BOS der correction_buffer korrekte Zeitstempel hat
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("SEQUENZ 5: Detaillierter Trace – was landet in inner_structure nach BOS?")
print("=" * 60)

# Das kritischste Szenario:
# SHORT: H0(1000), LL=L1(1100), dann correction_buffer füllt sich:
#   H1(1200), L2(1300, kein neues LL), H2(1400)
# BOS bei H3(1500) > bos_level
# impulse_start = L1 (t=1100)
# correction_buffer = [H1(1200), L2(1300), H2(1400)]
# Replay: queue bekommt [H1, L2, H2] vorne dran
# LONG-Kontext: H1(1200) → erstes HH → inner.append(impulse_start=L1@1100, H1@1200)
#   → 1100 < 1200: OK!
# Dann L2(1300) → lowest_since_hh = L2
# Dann H2(1400) > H1(1200) → neues HH → inner.append(L2@1300, H2@1400): OK!

pivots_5 = [
    make_pivot(1000, 100, True),    # H0 – init SHORT
    make_pivot(1100,  80, False),   # L1 – LL, last_conf=L1
    make_pivot(1200,  88, True),    # H1 – correction, buf=[H1], highest=H1
    make_pivot(1300,  84, False),   # L2 – NOT new LL (84>80), buf=[H1,L2]
    make_pivot(1400,  92, True),    # H2 – correction, buf=[H1,L2,H2], highest=H2
    make_pivot(1500, 105, True),    # H3 – BOS UP (>100)
    make_pivot(1600,  98, False),   # L4 – pullback
    make_pivot(1700, 110, True),    # H4 – HH
]

level1_5, inner_5, temp_5 = compute_master_structure(pivots_5)
print("\n=== INNER STRUCTURE (Seq 5) ===")
for i, p in enumerate(inner_5):
    print(f"  [{i}]  t={int(p.time.timestamp())}  {'HIGH' if p.is_high else 'LOW ':4s}  @ {p.price}")

inner_dicts_5 = [{"time": int(p.time.timestamp()), "price": p.price} for p in inner_5]
sorted_5 = sorted(inner_dicts_5, key=lambda x: (x["time"], x["price"]))
filtered_5 = [
    p for i, p in enumerate(sorted_5)
    if i == 0 or p["time"] != sorted_5[i - 1]["time"] or p["price"] != sorted_5[i - 1]["price"]
]
print(f"\nNach Filter: {len(filtered_5)} Punkte")
for i, p in enumerate(filtered_5):
    print(f"  [{i}]  t={p['time']}  price={p['price']}")

print("\n=== STRICT ORDER NACH FILTER (Seq 5) ===")
strict_ok = True
for i in range(1, len(filtered_5)):
    if filtered_5[i]["time"] <= filtered_5[i - 1]["time"]:
        print(f"  FEHLER: [{i}] t={filtered_5[i]['time']} <= [{i-1}] t={filtered_5[i-1]['time']}")
        strict_ok = False
if strict_ok:
    print("  OK")

# ---------------------------------------------------------------------------
# ERGEBNIS-ZUSAMMENFASSUNG
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("ZUSAMMENFASSUNG")
print("=" * 60)
print(f"Seq 1: inner={len(inner)} Punkte, nach Filter={len(filtered)} Punkte")
print(f"Seq 2: inner={len(inner_2)} Punkte, nach Filter={len(filtered_2)} Punkte")
print(f"Seq 3: inner={len(inner_3)} Punkte, nach Filter={len(filtered_3)} Punkte")
print(f"Seq 4: inner={len(inner_4)} Punkte")
print(f"Seq 5: inner={len(inner_5)} Punkte, nach Filter={len(filtered_5)} Punkte")

# Prüfe ob inner_structure LEER ist (das wäre der Hauptbug)
print(f"\nIst inner_structure leer?")
print(f"  Seq1: {'JA – BUG!' if len(inner) == 0 else f'Nein ({len(inner)} Punkte)'}")
print(f"  Seq3: {'JA – BUG!' if len(inner_3) == 0 else f'Nein ({len(inner_3)} Punkte)'}")
print(f"  Seq5: {'JA – BUG!' if len(inner_5) == 0 else f'Nein ({len(inner_5)} Punkte)'}")
