"""
Indikator-Framework – eigene Python-Indikatoren hier implementieren.

Basis-Klasse: IndicatorBase
  calculate(df: pd.DataFrame) → pd.Series

Fertige Beispiele:
  SMA(period)   – Simple Moving Average
  EMA(period)   – Exponential Moving Average

Integration ins Backend:
  GET /api/indicator?name=SMA&period=50&symbol=XAUUSD&timeframe=5m
  → gibt [{time: unix, value: float}, ...] zurück (direkt in chart.js nutzbar)

Eigene Indikatoren: Klasse erben von IndicatorBase, in REGISTRY registrieren.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# Basis-Klasse
# ─────────────────────────────────────────────────────────────────────────────

class IndicatorBase(ABC):
    """
    Abstrakte Basis für alle Indikatoren.

    Implementiere calculate() in der Unterklasse.
    Der DataFrame hat Spalten: time (Unix int), open, high, low, close, volume.
    """

    @abstractmethod
    def calculate(self, df: pd.DataFrame) -> pd.Series:
        """
        Berechnet den Indikator und gibt eine pd.Series zurück.
        Index muss mit df.index übereinstimmen.
        NaN-Werte am Anfang (Warm-up-Phase) sind erlaubt.
        """

    def to_json(self, df: pd.DataFrame) -> list[dict]:
        """
        Berechnet den Indikator und gibt ihn als JSON-Liste zurück:
        [{"time": unix_int, "value": float}, ...]
        Werte mit NaN werden herausgefiltert.
        """
        series = self.calculate(df)
        result = []
        for i, (_, row) in enumerate(df.iterrows()):
            val = series.iloc[i] if i < len(series) else float("nan")
            if pd.isna(val):
                continue
            result.append({"time": int(row["time"]), "value": round(float(val), 6)})
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Fertige Indikatoren
# ─────────────────────────────────────────────────────────────────────────────

class SMA(IndicatorBase):
    """Simple Moving Average über den Close-Kurs."""

    def __init__(self, period: int = 50) -> None:
        if period < 1:
            raise ValueError("SMA period muss ≥ 1 sein.")
        self.period = period

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        return df["close"].rolling(window=self.period).mean()

    def __repr__(self) -> str:
        return f"SMA(period={self.period})"


class EMA(IndicatorBase):
    """Exponential Moving Average über den Close-Kurs."""

    def __init__(self, period: int = 20) -> None:
        if period < 1:
            raise ValueError("EMA period muss ≥ 1 sein.")
        self.period = period

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        return df["close"].ewm(span=self.period, adjust=False).mean()

    def __repr__(self) -> str:
        return f"EMA(period={self.period})"


class RSI(IndicatorBase):
    """
    Relative Strength Index (Wilder's Smoothing).
    Gibt Werte 0–100 zurück, erste `period` Werte sind NaN.
    """

    def __init__(self, period: int = 14) -> None:
        if period < 2:
            raise ValueError("RSI period muss ≥ 2 sein.")
        self.period = period

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.ewm(alpha=1 / self.period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / self.period, adjust=False).mean()

        # Wenn avg_loss = 0 → nur Gewinne → RSI = 100
        rs  = avg_gain / avg_loss.where(avg_loss != 0, other=np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi = rsi.where(avg_loss != 0, other=100.0)
        return rsi

    def __repr__(self) -> str:
        return f"RSI(period={self.period})"


# ─────────────────────────────────────────────────────────────────────────────
# Registry – hier neue Indikatoren eintragen
# ─────────────────────────────────────────────────────────────────────────────

REGISTRY: dict[str, type[IndicatorBase]] = {
    "SMA": SMA,
    "EMA": EMA,
    "RSI": RSI,
    # "MACD": MACD,   ← Eigene Indikatoren hier ergänzen
}


def build_indicator(name: str, **params) -> IndicatorBase:
    """
    Erstellt einen Indikator anhand seines Namens und optionaler Parameter.

    Beispiel:
        ind = build_indicator("SMA", period=50)
        data = ind.to_json(df)
    """
    cls = REGISTRY.get(name.upper())
    if cls is None:
        available = ", ".join(REGISTRY.keys())
        raise ValueError(f"Unbekannter Indikator '{name}'. Verfügbar: {available}")

    # Nur Parameter übergeben, die die Klasse kennt
    import inspect
    sig = inspect.signature(cls.__init__)
    valid = {k: v for k, v in params.items() if k in sig.parameters}
    logger.debug(f"Indikator erstellt: {name}({valid})")
    return cls(**valid)


def candles_to_df(candles: list[dict]) -> pd.DataFrame:
    """
    Konvertiert die JSON-Candle-Liste aus MT5Client in einen DataFrame.
    Spalten: time (Unix int), open, high, low, close, volume.
    """
    if not candles:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    return pd.DataFrame(candles)
