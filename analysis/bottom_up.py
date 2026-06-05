"""
bottom_up.py – Fraktale Bottom-Up Struktur-Engine.

Startet mit M1-Kerzen als Basis (Level 0 = Lila Micro),
baut daraus Level 1, Level 2, Level 3 durch iterative Anwendung
von compute_master_structure() auf die jeweils bestätigten Pivot-Punkte
der darunter liegenden Ebene.

Ebenen:
    Level 0 (Lila):   Rohe Micro-Pivots aus M1-Kerzen (filter_alternating)
    Level 1 (Cyan):   Bestätigte Pushes aus Level-0-Pivots
    Level 2 (Orange): Bestätigte Pushes aus Level-1-Pivots
    Level 3 (Grün):   Bestätigte Pushes aus Level-2-Pivots

Kein fester Timeframe-Anker (kein H4). Die Hierarchie entsteht
organisch aus dem Preisverhalten selbst.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from loguru import logger

from analysis.models import StructureState, dicts_to_candles, PivotPoint
from analysis.pivot import detect_pivot_high, detect_pivot_low
from analysis.structure import (
    update_micro_pivots,
    compute_master_structure,
    filter_alternating_pivots,
)

# Maximale Anzahl Ebenen
MAX_LEVELS = 3

# Mindestanzahl Pivots damit eine Ebene berechnet wird
MIN_PIVOTS_FOR_LEVEL = 4

# Minuten pro Timeframe-Label
TF_MINUTES: dict[str, int] = {
    "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440,
}

# Farben pro Ebene (zur Dokumentation, Frontend nutzt eigene Konstanten)
LEVEL_COLORS = {
    0: "#d44bec",   # Lila  – Micro
    1: "#00e5ff",   # Cyan  – Level 1
    2: "#f97316",   # Orange – Level 2
    3: "#4ade80",   # Grün  – Level 3
}


def compute_micro_pivots(
    candles: list,
    pivot_length: int = 2,
    tf_label: str = "1m",
) -> list[PivotPoint]:
    """
    Berechnet Micro-Pivots (Level 0) aus einer Kerzen-Liste.
    Entspricht der bestehenden Engine-Logik in StructureEngine.compute_pivots_for_candles().
    """
    if not candles:
        return []

    state = StructureState()
    max_i = len(candles) - pivot_length - 1

    for i in range(max_i + 1):
        candle = candles[i]
        ph = detect_pivot_high(candles, pivot_length, i)
        pl = detect_pivot_low(candles, pivot_length, i)

        handle_as_high = ph is not None
        handle_as_low = pl is not None

        # Wenn beide gleichzeitig: alternierend auflösen
        if ph is not None and pl is not None:
            if state.micro_pivots and state.micro_pivots[-1].is_high:
                handle_as_high, handle_as_low = False, True
            else:
                handle_as_high, handle_as_low = True, False

        if handle_as_high:
            update_micro_pivots(state.micro_pivots, candle.time, ph, True, tf_label)
        elif handle_as_low:
            update_micro_pivots(state.micro_pivots, candle.time, pl, False, tf_label)

    return filter_alternating_pivots(state.micro_pivots)


def build_bottom_up_levels(
    candles: list,
    pivot_length: int = 2,
    max_levels: int = MAX_LEVELS,
) -> dict:
    """
    Kernfunktion der Bottom-Up Engine.

    Nimmt eine Liste von Candle-Objekten (aus M1 oder beliebigem TF).
    Gibt ein Dict zurück:
    {
        "level_0": [PivotPoint, ...],   # Micro Pivots (Lila)
        "level_1": [PivotPoint, ...],   # Level 1 confirmed pivots (Cyan)
        "level_1_temp": [PivotPoint, ...],  # Level 1 unconfirmed correction
        "level_2": [PivotPoint, ...],   # Level 2 confirmed pivots (Orange)
        "level_2_temp": [PivotPoint, ...],
        "level_3": [PivotPoint, ...],   # Level 3 confirmed pivots (Grün)
        "level_3_temp": [PivotPoint, ...],
    }
    """
    result = {}

    # Level 0: Rohe Micro-Pivots aus Kerzen
    level_0 = compute_micro_pivots(candles, pivot_length)
    result["level_0"] = level_0

    current_pivots = level_0

    for level in range(1, max_levels + 1):
        if len(current_pivots) < MIN_PIVOTS_FOR_LEVEL:
            logger.debug(
                f"[BottomUp] Level {level}: nur {len(current_pivots)} Pivots, stoppe hier."
            )
            # Fehlende Ebenen als leer initialisieren
            for remaining in range(level, max_levels + 1):
                result[f"level_{remaining}"] = []
                result[f"level_{remaining}_temp"] = []
            break

        confirmed, _, temp = compute_master_structure(current_pivots)

        result[f"level_{level}"] = confirmed
        result[f"level_{level}_temp"] = temp

        logger.debug(
            f"[BottomUp] Level {level}: {len(confirmed)} confirmed pivots, "
            f"{len(temp)} temp pivots."
        )

        # Output dieser Ebene ist Input der nächsten
        # Wir kombinieren confirmed + temp für maximale Datentiefe
        next_input = list(confirmed)
        if temp:
            last_ts = confirmed[-1].time.timestamp() if confirmed else 0
            for p in temp:
                if p.time.timestamp() > last_ts:
                    next_input.append(p)

        current_pivots = filter_alternating_pivots(next_input)

    return result


def levels_to_dicts(levels: dict, chart_tf: str = "1m") -> dict:
    """
    Konvertiert alle PivotPoint-Listen im Result-Dict in JSON-serialisierbare Dicts.

    WICHTIG – Timestamp-Snapping:
      Die Pivots stammen aus M1-Kerzen. Ihr Timestamp ist ein M1-Slot
      (z.B. 13:43:00). Auf einem M5-Chart existiert dieser Slot nicht –
      LWC würde leere Ghost-Bars einfügen → Gaps zwischen den Candles.

      Fix: Jeden Timestamp VOR dem JSON-Output auf den Chart-TF-Slot
      abrunden:
        snapped = (ts // step_sec) * step_sec

      Mehrere M1-Pivots die nach dem Snapping auf denselben Slot fallen
      → der zeitlich späteste gewinnt (Dict-Überschreibung nach sort).
      Danach nochmals deduplizieren damit LWC strikte Monotonie bekommt.
    """
    step_sec = TF_MINUTES.get(chart_tf, 1) * 60  # z.B. M5 → 300 Sekunden

    output = {}
    for key, pivots in levels.items():
        # Erst zeitlich sortieren damit späterer Pivot bei Kollision gewinnt
        sorted_pivots = sorted(pivots, key=lambda x: x.time)

        slot_map: dict[int, dict] = {}  # snapped_ts → letztes Pivot-Dict
        for p in sorted_pivots:
            ts      = int(p.time.timestamp())
            snapped = (ts // step_sec) * step_sec   # auf TF-Slot abrunden
            slot_map[snapped] = {
                "time":     snapped,
                "time_iso": p.time.isoformat(),
                "price":    p.price,
                "is_high":  p.is_high,
                "tf":       p.tf,
            }

        # Aufsteigend sortiert ausgeben (LWC-Pflicht)
        output[key] = sorted(slot_map.values(), key=lambda x: x["time"])

    return output


def get_trend_from_level(pivots: list) -> str:
    """
    Leitet den aktuellen Trend aus den letzten zwei Pivots einer Ebene ab.
    """
    if len(pivots) < 2:
        return "Neutral"
    last_price = pivots[-1].price if hasattr(pivots[-1], "price") else pivots[-1]["price"]
    prev_price = pivots[-2].price if hasattr(pivots[-2], "price") else pivots[-2]["price"]
    return "Bullish" if last_price > prev_price else "Bearish"
