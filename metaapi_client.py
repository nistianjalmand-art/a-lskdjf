"""
MT5-Client – Wrapper für historische Daten und Live-Streaming via MetaTrader5.

Verbindet sich direkt mit dem lokal laufenden MT5-Terminal.
Kein Cloud-Dienst, kein API-Key erforderlich.

Voraussetzungen:
  - MetaTrader5 muss auf demselben Windows-Rechner laufen
  - Terminal muss geöffnet und eingeloggt sein, bevor das Backend startet
  - pip install MetaTrader5

Nutzung:
    client = MT5Client()
    await client.initialize()
    candles = await client.get_historical_candles("XAUUSD", "5m", 500)
    await client.subscribe_live("XAUUSD", my_callback)
"""

import asyncio
from datetime import datetime, timezone
from typing import Callable, Optional
from loguru import logger


class MT5Client:
    """Singleton-artiger Client – einmal initialisieren, überall nutzen."""

    def __init__(self) -> None:
        self._mt5 = None
        self._connected: bool = False
        self._listeners: dict[str, list[Callable]] = {}
        self._poll_tasks: dict[str, asyncio.Task] = {}
        self._symbol_map: dict[str, str] = {}   # public name → broker name

    # ── Öffentliche API ───────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """
        Verbindet mit dem lokal laufenden MT5-Terminal.

        WICHTIG: mt5.initialize() wird OHNE jegliche Argumente aufgerufen.
        Werden login/password/server/path übergeben, versucht die MT5-Library
        eine Neu-Authentifizierung gegen das Terminal, was bei bereits eingeloggten
        Sessions mit "Authorization failed (-6)" fehlschlägt.
        Ohne Argumente attached die Library sich einfach an die laufende Session.
        """
        try:
            import MetaTrader5 as mt5
        except ImportError:
            raise RuntimeError(
                "MetaTrader5-Package nicht gefunden. "
                "Bitte 'pip install MetaTrader5' auf dem Windows-Rechner ausführen."
            )

        self._mt5 = mt5
        loop = asyncio.get_event_loop()

        logger.info("MT5: Verbinde mit laufendem Terminal (keine Argumente) …")

        success = await loop.run_in_executor(None, mt5.initialize)

        if not success:
            error = mt5.last_error()
            raise RuntimeError(
                f"mt5.initialize() fehlgeschlagen: {error}\n"
                "Mögliche Ursachen:\n"
                "  • MT5-Terminal ist nicht geöffnet oder nicht eingeloggt\n"
                "  • Python und MT5 laufen mit unterschiedlichen Rechten "
                "(beide als Admin oder beide als User starten)\n"
                "  • 'Algo Trading' im Terminal deaktiviert (Toolbar-Button prüfen)"
            )

        # Terminal-Info für Log
        info = await loop.run_in_executor(None, mt5.terminal_info)
        if info:
            logger.success(
                f"MT5: Verbunden ✓  |  "
                f"Terminal: {info.name}  |  "
                f"Algo-Trading: {info.trade_allowed}  |  "
                f"Connected: {info.connected}"
            )
        else:
            logger.success("MT5: Verbunden ✓")

        self._connected = True

    async def get_symbol_name(self, base: str) -> str:
        """
        Gibt den Broker-internen Symbol-Namen für einen öffentlichen Basis-Namen zurück.
        Beispiel: "XAUUSD" → "XAUUSD.s"

        Strategie:
          1. Exakter Match  – Broker kennt das Symbol genau so
          2. Prefix-Match   – kürzester Name der mit `base` beginnt (z.B. "XAUUSD.s")
          3. Fallback       – `base` unverändert + Warning im Log
        Aktiviert das Symbol via mt5.symbol_select(..., True).
        """
        self._require_connected()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self._find_broker_symbol(base)
        )

    async def get_historical_candles(
        self,
        symbol: str,
        timeframe: str,
        count: int = 500,
    ) -> list[dict]:
        """
        Gibt `count` OHLCV-Bars für `symbol` und `timeframe` zurück.
        Format: [{"time": int (Unix), "open": f, "high": f, "low": f, "close": f, "volume": f}]
        Kerzen sind chronologisch sortiert (älteste zuerst).
        """
        self._require_connected()
        mt5 = self._mt5
        tf_constant = self._get_tf_constant(timeframe)
        loop = asyncio.get_event_loop()
        broker_symbol = await self._resolve_symbol(symbol)

        logger.debug(f"History: {symbol} {timeframe} ×{count} (MT5 TF Constant: {tf_constant})")

        rates = await loop.run_in_executor(
            None,
            lambda: mt5.copy_rates_from_pos(broker_symbol, tf_constant, 0, count),
        )

        if rates is None or len(rates) == 0:
            error = mt5.last_error()
            logger.error(
                f"copy_rates_from_pos({broker_symbol}, {timeframe}) fehlgeschlagen: {error}"
            )
            return []

        candles = [
            {
                "time":   int(r["time"]),
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": float(r["tick_volume"]),
            }
            for r in rates
        ]

        # MT5 liefert bereits chronologisch, aber explizit sicherstellen
        candles.sort(key=lambda x: x["time"])
        logger.info(f"History: {len(candles)} Kerzen geladen ({symbol} {timeframe})")
        return candles

    async def get_historical_candles_offset(
        self,
        symbol: str,
        timeframe: str,
        offset: int = 0,
        count: int = 500,
    ) -> list[dict]:
        """
        Gibt `count` OHLCV-Bars für `symbol` und `timeframe` zurück,
        mit einem Versatz von `offset` Kerzen in die Vergangenheit.
        Wird für das Lazy-Loading beim Zurückscrollen genutzt.
        """
        self._require_connected()
        mt5 = self._mt5
        tf_constant = self._get_tf_constant(timeframe)
        loop = asyncio.get_event_loop()
        broker_symbol = await self._resolve_symbol(symbol)

        logger.info(f"History Pagination: {symbol} {timeframe} offset={offset} count={count}")

        rates = await loop.run_in_executor(
            None,
            lambda: mt5.copy_rates_from_pos(broker_symbol, tf_constant, offset, count),
        )

        if rates is None or len(rates) == 0:
            return []

        candles = [
            {
                "time":   int(r["time"]),
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": float(r["tick_volume"]),
            }
            for r in rates
        ]

        candles.sort(key=lambda x: x["time"])
        return candles

    async def get_historical_candles_range(
        self,
        symbol: str,
        timeframe: str,
        from_time: datetime,
        to_time: datetime,
    ) -> list[dict]:
        """
        Gibt alle OHLCV-Bars für `symbol` und `timeframe` im Zeitraum
        [from_time, to_time] zurück. Nutzt MT5's copy_rates_range.

        Ideal für die Smart-Loading-Engine: Lädt exakt die Kerzen eines
        H4-Segments (von Pivot A bis Pivot B), ohne unnötige Daten.

        Format: [{"time": int (Unix), "open": f, "high": f, "low": f, "close": f, "volume": f}]
        Kerzen sind chronologisch sortiert (älteste zuerst).
        """
        self._require_connected()
        mt5 = self._mt5
        tf_constant = self._get_tf_constant(timeframe)
        loop = asyncio.get_event_loop()
        broker_symbol = await self._resolve_symbol(symbol)

        # MT5 erwartet naive datetime-Objekte in UTC (ohne tzinfo)
        from_naive = from_time.replace(tzinfo=None) if from_time.tzinfo else from_time
        to_naive   = to_time.replace(tzinfo=None)   if to_time.tzinfo   else to_time

        logger.debug(
            f"History range: {symbol} {timeframe} "
            f"[{from_naive.strftime('%Y-%m-%d %H:%M')} → {to_naive.strftime('%Y-%m-%d %H:%M')}]"
        )

        rates = await loop.run_in_executor(
            None,
            lambda: mt5.copy_rates_range(broker_symbol, tf_constant, from_naive, to_naive),
        )

        if rates is None or len(rates) == 0:
            error = mt5.last_error()
            logger.warning(
                f"copy_rates_range({broker_symbol}, {timeframe}, "
                f"{from_naive} → {to_naive}) leer oder fehlgeschlagen: {error}"
            )
            return []

        candles = [
            {
                "time":   int(r["time"]),
                "open":   float(r["open"]),
                "high":   float(r["high"]),
                "low":    float(r["low"]),
                "close":  float(r["close"]),
                "volume": float(r["tick_volume"]),
            }
            for r in rates
        ]

        candles.sort(key=lambda x: x["time"])
        logger.info(
            f"History range: {len(candles)} Kerzen geladen "
            f"({symbol} {timeframe}, {from_naive.strftime('%Y-%m-%d')} → {to_naive.strftime('%Y-%m-%d')})"
        )
        return candles

    async def get_symbols(self) -> list[str]:
        """
        Gibt alle verfügbaren Symbole des verbundenen Brokers zurück.
        Fallback auf leere Liste bei Fehler.
        """
        self._require_connected()
        mt5 = self._mt5
        loop = asyncio.get_event_loop()

        try:
            symbols = await loop.run_in_executor(None, mt5.symbols_get)
            if symbols is None:
                return []
            return sorted(s.name for s in symbols)
        except Exception as exc:
            logger.error(f"get_symbols fehlgeschlagen: {exc}")
            return []

    async def subscribe_live(self, symbol: str, callback: Callable) -> None:
        """
        Registriert einen Live-Tick-Callback für `symbol`.
        callback(symbol, mid, bid, ask, time) wird bei jedem neuen Tick aufgerufen.

        Intern wird ein Polling-Task gestartet, der alle 500 ms via
        mt5.symbol_info_tick() den aktuellen Tick abfragt.
        """
        self._require_connected()

        if symbol not in self._listeners:
            self._listeners[symbol] = []

        if callback not in self._listeners[symbol]:
            self._listeners[symbol].append(callback)
            logger.debug(
                f"Listener registriert für {symbol} "
                f"(gesamt: {len(self._listeners[symbol])})"
            )

        # Polling-Task starten, falls noch keiner läuft
        task = self._poll_tasks.get(symbol)
        if task is None or task.done():
            self._poll_tasks[symbol] = asyncio.create_task(
                self._poll_loop(symbol)
            )
            logger.info(f"Live-Polling gestartet: {symbol}")

    async def unsubscribe_live(self, symbol: str, callback: Callable) -> None:
        """Entfernt einen Callback. Wenn kein Listener mehr vorhanden → Polling stoppen."""
        if symbol in self._listeners:
            try:
                self._listeners[symbol].remove(callback)
            except ValueError:
                pass

            if not self._listeners[symbol]:
                del self._listeners[symbol]
                task = self._poll_tasks.pop(symbol, None)
                if task and not task.done():
                    task.cancel()
                logger.info(f"Live-Polling beendet: {symbol}")

    async def shutdown(self) -> None:
        """Sauber trennen beim App-Stop."""
        logger.info("MT5: Verbindung wird getrennt …")

        # Alle Polling-Tasks stoppen
        for task in self._poll_tasks.values():
            if not task.done():
                task.cancel()
        self._poll_tasks.clear()
        self._listeners.clear()

        if self._mt5 is not None and self._connected:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._mt5.shutdown)

        self._connected = False
        logger.info("MT5: Getrennt.")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Intern ───────────────────────────────────────────────────────────────

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError(
                "MT5Client ist nicht verbunden. initialize() zuerst aufrufen."
            )

    def _find_broker_symbol(self, base: str) -> str:
        """
        Synchrone Suche nach dem Broker-internen Symbol-Namen (läuft im Executor).
        Aktiviert das Symbol via symbol_select(..., True) damit MT5 Daten liefert.
        """
        mt5 = self._mt5
        all_syms = mt5.symbols_get()
        names = [s.name for s in all_syms] if all_syms else []

        if base in names:
            resolved = base
        else:
            candidates = [n for n in names if n.upper().startswith(base.upper())]
            if candidates:
                resolved = min(candidates, key=len)   # kürzester = direktester Match
                logger.info(f"Symbol '{base}' → Broker-Name '{resolved}'")
            else:
                logger.warning(f"Symbol '{base}' nicht im Broker gefunden – verwende unverändert")
                resolved = base

        mt5.symbol_select(resolved, True)
        return resolved

    async def _resolve_symbol(self, public_name: str) -> str:
        """
        Gibt den gecachten Broker-Namen für `public_name` zurück.
        Beim ersten Aufruf pro Symbol wird _find_broker_symbol() im Executor ausgeführt.
        """
        if public_name not in self._symbol_map:
            loop = asyncio.get_event_loop()
            resolved = await loop.run_in_executor(
                None, lambda: self._find_broker_symbol(public_name)
            )
            self._symbol_map[public_name] = resolved
        return self._symbol_map[public_name]

    def _get_tf_constant(self, timeframe: str) -> int:
        """Konvertiert einen Timeframe-String in die entsprechende MT5-Konstante."""
        mt5 = self._mt5
        mapping: dict[str, int] = {
            "1m":  mt5.TIMEFRAME_M1,
            "5m":  mt5.TIMEFRAME_M5,
            "15m": mt5.TIMEFRAME_M15,
            "30m": mt5.TIMEFRAME_M30,
            "1h":  mt5.TIMEFRAME_H1,
            "4h":  mt5.TIMEFRAME_H4,
            "1d":  mt5.TIMEFRAME_D1,
            "1w":  mt5.TIMEFRAME_W1,
        }
        tf = mapping.get(timeframe)
        if tf is None:
            logger.error(f"Mapping failed for timeframe: '{timeframe}'. Available: {list(mapping.keys())}")
            raise ValueError(
                f"Unbekannter Timeframe: '{timeframe}'. "
                f"Erlaubt: {list(mapping.keys())}"
            )
        return tf

    async def _poll_loop(self, symbol: str) -> None:
        """
        Hintergrund-Task: Fragt alle 500 ms den aktuellen Tick ab.
        Ruft alle registrierten Callbacks auf, wenn sich der Tick geändert hat.
        """
        mt5 = self._mt5
        loop = asyncio.get_event_loop()
        last_tick_time: Optional[int] = None
        broker_symbol = await self._resolve_symbol(symbol)

        while symbol in self._listeners and self._listeners[symbol]:
            try:
                tick = await loop.run_in_executor(
                    None, lambda: mt5.symbol_info_tick(broker_symbol)
                )

                if tick is not None and tick.time != last_tick_time:
                    last_tick_time = tick.time
                    tick_dt = datetime.fromtimestamp(tick.time, tz=timezone.utc)
                    bid = float(tick.bid)
                    ask = float(tick.ask)
                    mid = (bid + ask) / 2

                    for cb in list(self._listeners.get(symbol, [])):
                        try:
                            await cb(symbol, mid, bid, ask, tick_dt)
                        except Exception as exc:
                            logger.warning(f"Listener-Fehler für {symbol}: {exc}")

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Poll-Loop-Fehler ({symbol}): {exc}")

            await asyncio.sleep(0.5)  # 500 ms Polling-Intervall


# ─────────────────────────────────────────────────────────────────────────────
# Globale Singleton-Instanz (wird von main.py importiert)
# ─────────────────────────────────────────────────────────────────────────────
mt5client = MT5Client()

# Alias für Kompatibilität mit main.py (importiert 'metaapi')
metaapi = mt5client
