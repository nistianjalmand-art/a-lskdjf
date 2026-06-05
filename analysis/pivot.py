"""
Pivot-Erkennung – entspricht ta.pivothigh() / ta.pivotlow() im TV-Skript.

Ein Pivot High bei Index i ist bestätigt wenn:
  high[i] = max(high[i-length : i+length+1])

Wichtig: Pivots sind erst NACH 'length' weiteren Kerzen bestätigt.
D.h. beim aktuellen Stand kennen wir den letzten Pivot erst [length] Bars zurück.
"""
from analysis.models import Candle, PivotPoint


def detect_pivot_high(candles: list[Candle], length: int, index: int) -> float | None:
    """
    Prüft ob candles[index] ein Pivot High ist.
    Gibt den High-Preis zurück, oder None.

    Entspricht: ta.pivothigh(high, length, length) auf Bar [index]
    """
    if index < length or index + length >= len(candles):
        return None

    center_high = candles[index].high
    for i in range(index - length, index + length + 1):
        if i == index:
            continue
        if candles[i].high >= center_high:
            return None
    return center_high


def detect_pivot_low(candles: list[Candle], length: int, index: int) -> float | None:
    """
    Prüft ob candles[index] ein Pivot Low ist.
    Gibt den Low-Preis zurück, oder None.

    Entspricht: ta.pivotlow(low, length, length) auf Bar [index]
    """
    if index < length or index + length >= len(candles):
        return None

    center_low = candles[index].low
    for i in range(index - length, index + length + 1):
        if i == index:
            continue
        if candles[i].low <= center_low:
            return None
    return center_low


def compute_all_pivots(candles: list[Candle], length: int) -> list[PivotPoint]:
    """
    Berechnet alle Pivot Highs und Lows für eine Kerzen-Liste.
    Gibt eine zeitlich sortierte Liste von PivotPoints zurück.

    Hinweis: Die letzten 'length' Kerzen können noch keinen bestätigten Pivot haben.
    """
    pivots = []
    for i in range(len(candles)):
        ph = detect_pivot_high(candles, length, i)
        pl = detect_pivot_low(candles, length, i)

        if ph is not None:
            pivots.append(PivotPoint(time=candles[i].time, price=ph, is_high=True))
        if pl is not None:
            pivots.append(PivotPoint(time=candles[i].time, price=pl, is_high=False))

    pivots.sort(key=lambda p: p.time)
    return pivots
