"""
Datenmodelle – extrem reduziert auf Basis Micro-Pivots für V4 (Top-Down H4).
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Candle:
    """Eine einzelne OHLCV-Kerze."""
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class PivotPoint:
    """
    Entspricht 'type PivotPoint' im TV-Skript.
    is_high=True  → Pivot High
    is_high=False → Pivot Low
    """
    time: datetime
    price: float
    is_high: bool
    tf: str = "" # Für H4 Unterscheidung im Frontend

    def __repr__(self):
        kind = "HIGH" if self.is_high else "LOW"
        return f"Pivot[{kind} @ {self.price:.4f} | {self.time}]"

    def to_dict(self) -> dict:
        return {
            "time":     int(self.time.timestamp()),
            "time_iso": self.time.isoformat(),
            "price":    self.price,
            "is_high":  self.is_high,
            "tf":       self.tf,
        }


@dataclass
class StructureState:
    """
    Auf Micro-Pivots reduzierte Struktur-State Klasse.
    """
    micro_pivots: list = field(default_factory=list)
    h4_master_pivots: list = field(default_factory=list)


@dataclass
class AlertEvent:
    """Ein Alert-Event, das ans Telegram gesendet wird."""
    symbol: str
    timeframe: str
    event_type: str
    price: float
    time: datetime
    details: str = ""

    def to_dict(self) -> dict:
        return {
            "type":     self.event_type,
            "symbol":   self.symbol,
            "timeframe": self.timeframe,
            "price":    self.price,
            "time":     int(self.time.timestamp()),
            "time_iso": self.time.isoformat(),
            "details":  self.details,
        }


def dicts_to_candles(candle_dicts: list[dict]) -> list[Candle]:
    """Konvertiert MT5-Candle-Dicts in Candle-Objekte."""
    return [
        Candle(
            time=datetime.fromtimestamp(d["time"], tz=timezone.utc),
            open=float(d["open"]),
            high=float(d["high"]),
            low=float(d["low"]),
            close=float(d["close"]),
            volume=float(d["volume"]),
        )
        for d in candle_dicts
    ]
