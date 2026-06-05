"""
FastAPI-Server – Trading Dashboard Backend.

Endpoints:
  GET  /api/history?symbol=XAUUSD&timeframe=5m&count=500   → historische Candles
  GET  /api/structure?symbol=XAUUSD&timeframe=5m&count=300 → Pivot/BOS-Analyse
  GET  /api/symbols                                         → verfügbare Symbole
  GET  /api/timeframes                                      → verfügbare Timeframes
  GET  /api/indicator?name=SMA&period=50&symbol=...&tf=...  → Indikatorwerte
  GET  /api/status                                          → Verbindungsstatus
  GET  /api/monitor/status                                  → Background-Monitor-Status
  WS   /ws/live?symbol=XAUUSD                              → Live-Ticks als CandleUpdate

Starten:
  uvicorn main:app --reload --port 8000
  → http://localhost:8000
"""

import asyncio
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from loguru import logger

from config import (
    DEFAULT_SYMBOL, DEFAULT_TIMEFRAME, DEFAULT_CANDLE_COUNT, TIMEFRAMES,
    PIVOT_LENGTH,
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED,
    MONITOR_SYMBOLS, MONITOR_TIMEFRAMES, MONITOR_CANDLE_COUNT, MONITOR_INTERVAL_SECONDS,
)
from metaapi_client import metaapi
from indicators import build_indicator, candles_to_df
from analysis.engine import StructureEngine
from alerts.telegram import TelegramAlerter


# ─────────────────────────────────────────────────────────────────────────────
# Globale Instanzen
# ─────────────────────────────────────────────────────────────────────────────

structure_engine = StructureEngine(pivot_length=PIVOT_LENGTH)



# ─────────────────────────────────────────────────────────────────────────────
# App-Lifecycle (startup / shutdown)
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """MT5-Client initialisieren."""
    try:
        await metaapi.initialize()
    except Exception as exc:
        logger.error(f"MT5 konnte nicht initialisiert werden: {exc}")
        logger.warning("Server startet trotzdem – API-Calls werden Fehler zurückgeben.")

    yield
    await metaapi.shutdown()


app = FastAPI(
    title="Trading Dashboard API",
    description="MetaTrader5 + FastAPI + Lightweight Charts",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS – für lokale Entwicklung alle Origins erlauben
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Statische Dateien (Frontend)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ─────────────────────────────────────────────────────────────────────────────
# Frontend
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    """Liefert das Chart-Frontend."""
    return FileResponse("static/index.html")


# ─────────────────────────────────────────────────────────────────────────────
# REST Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/history")
async def get_history(
    symbol:    str = Query(default=DEFAULT_SYMBOL,       description="z.B. XAUUSD, EURUSD"),
    timeframe: str = Query(default=DEFAULT_TIMEFRAME,    description="1m | 5m | 15m | 1h | 4h | 1d"),
    count:     int = Query(default=DEFAULT_CANDLE_COUNT, ge=1, le=5000, description="Anzahl Kerzen"),
    before:    Optional[int] = Query(None,               description="DEPRECATED: Benutze offset"),
    offset:    Optional[int] = Query(0,                  description="Versatz von der aktuellen Kerze (0) in die Vergangenheit"),
):
    """
    Liefert historische OHLCV-Candles für ein Symbol.

    Rückgabe-Format (Lightweight Charts kompatibel):
    [{"time": unix_int, "open": f, "high": f, "low": f, "close": f, "volume": f}, ...]
    """
    if not metaapi.is_connected:
        raise HTTPException(503, detail="MT5 nicht verbunden.")

    logger.info(f"API: get_history symbol={symbol}, timeframe={timeframe}, count={count}, offset={offset}")

    if offset > 0:
        candles = await metaapi.get_historical_candles_offset(symbol, timeframe, offset, count)
    else:
        candles = await metaapi.get_historical_candles(symbol, timeframe, count)

    if not candles:
        return {"symbol": symbol, "timeframe": timeframe, "count": 0, "candles": []}

    return {"symbol": symbol, "timeframe": timeframe, "count": len(candles), "candles": candles}


@app.get("/api/structure")
async def get_structure(
    symbol:    str = Query(default=DEFAULT_SYMBOL,    description="z.B. XAUUSD, EURUSD"),
    timeframe: str = Query(default=DEFAULT_TIMEFRAME, description="1m | 5m | 15m | 1h | 4h | 1d"),
    count:     int = Query(default=300, ge=50, le=2000, description="Kerzen für die Analyse"),
    viewport_start: Optional[int] = Query(None, description="Startzeitpunkt des sichtbaren Bereichs (Unix Timestamp)"),
    viewport_end: Optional[int] = Query(None, description="Endzeitpunkt des sichtbaren Bereichs (Unix Timestamp)"),
    pivot_length: int = Query(default=PIVOT_LENGTH, ge=1, le=20, description="Pivot-Stärke (Kerzen links/rechts)"),
):
    """
    Berechnet die smarte dynamische Marktstruktur-Analyse für ein Symbol,
    basierend auf dem aktuellen Viewport.

    Enthält:
      - trend_direction: 1 (Up) oder -1 (Down) – nach letztem BOS
      - white_trend: 1 (Up), -1 (Down), 0 (Neutral) – HH/HL bzw. LH/LL Pattern
      - live_trend: aktueller Preis bricht Struktur bereits (ohne Kerzenschluss)
      - confirmed_high / confirmed_low: aktuelle Struktur-Level
      - potential_high / potential_low: noch nicht bestätigte Level
      - pivots: letzte 100 Micro-Pivots (für Chart-Darstellung)
      - bos_events: alle BOS-Events innerhalb der analysierten Kerzen
    """
    if not metaapi.is_connected:
        raise HTTPException(503, detail="MT5 nicht verbunden.")

    candles = await metaapi.get_historical_candles(symbol, timeframe, count)
    if not candles:
        raise HTTPException(404, detail=f"Keine Daten für {symbol} {timeframe}.")

    try:
        vp_start = viewport_start or 0
        vp_end = viewport_end or int(datetime.now(timezone.utc).timestamp())
        return await structure_engine.get_smart_structure(symbol, timeframe, vp_start, vp_end, count, pivot_length)
    except Exception as exc:
        logger.exception(f"Struktur-Analyse fehlgeschlagen: {symbol} {timeframe} | {exc}")
        raise HTTPException(500, detail=f"Analyse-Fehler: {str(exc)}")


@app.get("/api/symbols")
async def get_symbols():
    """
    Gibt alle verfügbaren Symbole des verbundenen Brokers zurück.
    Nützlich für den Symbol-Selector im Frontend.
    """
    if not metaapi.is_connected:
        raise HTTPException(503, detail="MT5 nicht verbunden.")

    symbols = await metaapi.get_symbols()
    return {"symbols": symbols, "default": DEFAULT_SYMBOL}


@app.get("/api/timeframes")
async def get_timeframes():
    """Verfügbare Timeframe-Buttons (statisch aus config.py)."""
    return {"timeframes": TIMEFRAMES, "default": DEFAULT_TIMEFRAME}


@app.get("/api/indicator")
async def get_indicator(
    name:      str           = Query(...,                description="SMA | EMA | RSI"),
    symbol:    str           = Query(DEFAULT_SYMBOL,     description="Symbol"),
    timeframe: str           = Query(DEFAULT_TIMEFRAME,  description="Timeframe"),
    count:     int           = Query(DEFAULT_CANDLE_COUNT, ge=10, le=5000),
    period:    Optional[int] = Query(None,               description="Perioden-Länge"),
):
    """
    Berechnet einen Indikator auf historischen Daten und gibt ihn als
    Linienpunkte zurück:
    [{"time": unix_int, "value": float}, ...]
    """
    if not metaapi.is_connected:
        raise HTTPException(503, detail="MT5 nicht verbunden.")

    candles = await metaapi.get_historical_candles(symbol, timeframe, count)
    if not candles:
        raise HTTPException(404, detail=f"Keine Daten für {symbol} {timeframe}.")

    try:
        params = {}
        if period is not None:
            params["period"] = period
        indicator = build_indicator(name, **params)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))

    df   = candles_to_df(candles)
    data = indicator.to_json(df)

    return {
        "indicator": name,
        "params":    {"period": period} if period else {},
        "symbol":    symbol,
        "timeframe": timeframe,
        "data":      data,
    }


@app.get("/api/status")
async def get_status():
    """Health-Check und Verbindungsstatus."""
    return {
        "connected":         metaapi.is_connected,
        "default_symbol":    DEFAULT_SYMBOL,
        "default_timeframe": DEFAULT_TIMEFRAME,
        "pivot_length":      PIVOT_LENGTH,
    }


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket – Live-Tick-Feed
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws/live")
async def websocket_live(
    websocket: WebSocket,
    symbol: str = Query(default=DEFAULT_SYMBOL),
):
    """
    Streamt Live-Preis-Updates für `symbol` an den Browser.

    Protokoll (JSON):
      → {"type": "tick",  "symbol": "XAUUSD", "bid": 2650.5, "ask": 2650.7,
                          "mid": 2650.6, "time": "2026-03-11T10:00:00+00:00"}
      → {"type": "error", "message": "..."}
      → {"type": "info",  "message": "Verbunden mit XAUUSD"}

    Das Frontend aggregiert Ticks selbst zu Bars oder zeigt nur den Bid/Ask-Preis.
    """
    await websocket.accept()
    logger.info(f"WebSocket verbunden: {symbol}")

    if not metaapi.is_connected:
        await websocket.send_json({"type": "error", "message": "MT5 nicht verbunden."})
        await websocket.close()
        return

    # Queue als Brücke zwischen MT5-Polling-Callback und WebSocket-Send
    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=500)

    async def on_tick(sym: str, mid: float, bid: float, ask: float, tick_time) -> None:
        try:
            queue.put_nowait({
                "type":   "tick",
                "symbol": sym,
                "bid":    round(bid, 6),
                "ask":    round(ask, 6),
                "mid":    round(mid, 6),
                "time":   tick_time.isoformat() if hasattr(tick_time, "isoformat") else str(tick_time),
            })
        except asyncio.QueueFull:
            logger.warning(f"WebSocket-Queue voll für {sym} – Tick verworfen")

    await metaapi.subscribe_live(symbol, on_tick)
    await websocket.send_json({"type": "info", "message": f"Live-Feed aktiv: {symbol}"})

    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json(msg)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})

    except WebSocketDisconnect:
        logger.info(f"WebSocket getrennt: {symbol}")
    except Exception as exc:
        logger.error(f"WebSocket-Fehler ({symbol}): {exc}")
    finally:
        await metaapi.unsubscribe_live(symbol, on_tick)
        logger.debug(f"WebSocket cleanup abgeschlossen: {symbol}")
