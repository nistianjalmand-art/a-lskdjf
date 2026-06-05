"""
engine_bottom_up.py – Async Wrapper für die Bottom-Up Engine.

Wird von main.py (FastAPI) unter /api/structure_bu aufgerufen.
Lädt M1-Kerzen, berechnet alle Level und gibt das Frontend-JSON zurück.

Adaptiver M1-Count:
  Je höher der angezeigte Chart-TF, desto mehr M1-Kerzen werden geladen,
  damit L1/L2/L3 genug historische Tiefe haben.

  TF    M1-Kerzen   Zeitraum (ca.)
  1m      800        13 Stunden
  5m     1500        25 Stunden
  15m    3000         2 Tage
  1h     6000         4 Tage
  4h    12000         8 Tage
  1d    20000        14 Tage
"""
from __future__ import annotations

import time
from loguru import logger

from analysis.models import dicts_to_candles
from analysis.bottom_up import build_bottom_up_levels, levels_to_dicts, get_trend_from_level

# Cache-TTL
CACHE_TTL = 30  # Sekunden

# Adaptiver M1-Count je nach angezeigtem Timeframe
M1_COUNT_BY_TF: dict[str, int] = {
    "1m":   800,
    "5m":  1500,
    "15m": 3000,
    "1h":  6000,
    "4h": 12000,
    "1d": 20000,
}
M1_COUNT_DEFAULT = 800


class BottomUpEngine:
    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}

    def _cache_get(self, key: str):
        entry = self._cache.get(key)
        if entry and (time.time() - entry["ts"]) < CACHE_TTL:
            return entry["data"]
        return None

    def _cache_set(self, key: str, data) -> None:
        self._cache[key] = {"ts": time.time(), "data": data}

    async def get_structure(
        self,
        symbol: str,
        timeframe: str,
        viewport_start: int,
        viewport_end: int,
        count: int | None = None,
        pivot_length: int = 2,
    ) -> dict:
        """
        Hauptmethode: Lädt M1-Kerzen (adaptiver Count je nach TF),
        berechnet alle Bottom-Up Level, gibt fertiges Frontend-JSON zurück.
        """
        from metaapi_client import metaapi

        # Adaptiver M1-Count: ignoriert den übergebenen count-Parameter
        # und verwendet stattdessen den TF-basierten Wert
        m1_count = M1_COUNT_BY_TF.get(timeframe, M1_COUNT_DEFAULT)

        # Cache-Key enthält TF damit M5 und H1 eigene Caches haben
        cache_key = f"bu_{symbol}_{timeframe}_{pivot_length}_{m1_count}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug(f"[BottomUpEngine] Cache hit: {cache_key}")
            return cached

        # M1-Kerzen laden (immer M1 als Basis der Bottom-Up-Pyramide)
        raw = await metaapi.get_historical_candles(symbol, "1m", m1_count)
        if not raw:
            logger.warning(f"[BottomUpEngine] Keine M1-Kerzen für {symbol}")
            return self._empty_response(symbol, timeframe)

        candles = dicts_to_candles(raw)
        logger.debug(
            f"[BottomUpEngine] {len(candles)} M1-Kerzen geladen für {symbol} "
            f"(TF={timeframe}, m1_count={m1_count})"
        )

        # Bottom-Up Levels berechnen
        levels = build_bottom_up_levels(candles, pivot_length=pivot_length)

        # In JSON-serialisierbare Dicts konvertieren
        levels_dict = levels_to_dicts(levels)

        # Viewport-Filterung: Nur Punkte die im Fenster (+ Puffer) liegen
        vp_pad = 86400 * 2  # 2 Tage Puffer
        for key in levels_dict:
            levels_dict[key] = [
                p for p in levels_dict[key]
                if (viewport_start - vp_pad) <= p["time"] <= (viewport_end + vp_pad)
            ] if viewport_start > 0 else levels_dict[key]

        # Trends aus den bestätigten Ebenen ableiten
        trend_1 = get_trend_from_level(levels.get("level_1", []))
        trend_2 = get_trend_from_level(levels.get("level_2", []))
        trend_3 = get_trend_from_level(levels.get("level_3", []))

        result = {
            "symbol":       symbol,
            "timeframe":    timeframe,
            "mode":         "bottom_up",
            "trend_l1":     trend_1,
            "trend_l2":     trend_2,
            "trend_l3":     trend_3,
            # Level 0: Micro Pivots (Lila)
            "level_0":      levels_dict.get("level_0", []),
            # Level 1: Erste Zusammenfassung (Cyan)
            "level_1":      levels_dict.get("level_1", []),
            "level_1_temp": levels_dict.get("level_1_temp", []),
            # Level 2: Zweite Zusammenfassung (Orange)
            "level_2":      levels_dict.get("level_2", []),
            "level_2_temp": levels_dict.get("level_2_temp", []),
            # Level 3: Dritte Zusammenfassung (Grün)
            "level_3":      levels_dict.get("level_3", []),
            "level_3_temp": levels_dict.get("level_3_temp", []),
        }

        self._cache_set(cache_key, result)
        return result

    @staticmethod
    def _empty_response(symbol: str, timeframe: str) -> dict:
        return {
            "symbol": symbol, "timeframe": timeframe, "mode": "bottom_up",
            "trend_l1": "Neutral", "trend_l2": "Neutral", "trend_l3": "Neutral",
            "level_0": [], "level_1": [], "level_1_temp": [],
            "level_2": [], "level_2_temp": [],
            "level_3": [], "level_3_temp": [],
        }
