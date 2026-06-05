/**
 * chart.js – TradingView Lightweight Charts Integration
 *
 * Verantwortlich für:
 *  - Chart initialisieren (Candlestick-Series)
 *  - Historische Daten von /api/history laden
 *  - Live-Updates per WebSocket (/ws/live) empfangen und aggregieren
 *  - Symbol- und Timeframe-Wechsel
 *  - Indikator-Linien über drawIndicatorLine() zeichnen
 *  - Struktur-Overlay: Micro-Pivots (lila), White ZigZag (weiß),
 *    Red Structure (rot), Zone Lines (gelb), Trend-Badge
 *
 * Öffentliche API (zum Einbinden eigener Indikatoren):
 *  drawIndicatorLine(name, data, color, lineWidth)
 *  removeIndicatorLine(name)
 *  clearAllIndicators()
 */

"use strict";

// ── Konfiguration ──────────────────────────────────────────────────────────────
const API_BASE  = "";          // leer = same origin
const WS_PROTO  = location.protocol === "https:" ? "wss:" : "ws:";
const WS_BASE   = `${WS_PROTO}//${location.host}`;

// ── State ──────────────────────────────────────────────────────────────────────
let chart          = null;     // IChartApi
let candleSeries   = null;     // ISeriesApi<"Candlestick">
let volumeSeries   = null;     // ISeriesApi<"Histogram">
let wsConnection   = null;     // WebSocket
let currentSymbol  = "XAUUSD";
let currentTF      = "5m";
window.currentTF   = currentTF; // Export für index.html
let liveCandle     = null;     // Aktuell laufende (unfertige) Kerze
let wsReconnectTimer = null;
let currentPivotLength = 2;    // Pivot-Stärke (Kerzen links/rechts)
let lastPrice          = 0;

// Lazy-Loading & Sync States
let isLoadingHistory  = false;
let lastLoadTimestamp = 0; // Cooldown-Timer gegen Loops
let historyEndReached = new Set(); // Speichert "Symbol_TF" wenn Ende erreicht
let allCandles       = [];     // Cache aller geladenen Kerzen (für TF-Sync & Prepend)
let structureDebounceTimer = null;

/**
 * ⚠️ KRITISCH – Nicht entfernen!
 * Dieser Flag verhindert die Haupt-Rückkopplungsschleife des Systems.
 *
 * PROBLEM: chart.addLineSeries() und series.setData() sind Chart-Mutationen,
 * die intern das subscribeVisibleTimeRangeChange-Event von LWC feuern können.
 * Das würde handleScroll() erneut aufrufen → loadStructure() → drawStructure()
 * → addLineSeries() → Event → ... (exponentiell beschleunigende Endlosschleife
 * die sich als "rückwärts springen in die Vergangenheit" manifestiert).
 *
 * LÖSUNG: Vor jeder Chart-Mutation (rebuildSeriesPool, clearStructure) wird
 * dieser Flag auf true gesetzt. handleScroll() prüft ihn als erstes und
 * verwirft den Event sofort, wenn eine Mutation im Gange ist.
 */
let isMutatingChart = false;

/** Map: name → ISeriesApi<"Line"> – verwaltet alle Indikator-Linien */
const indicatorSeries = new Map();
/** Map: name → { color, lineWidth } – Metadaten für die Legend */
const indicatorMeta   = new Map();

/** Minuten pro Timeframe (für Live-Bar-Aggregation) */
const TF_MINUTES = { "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440 };

// ── Struktur-State ─────────────────────────────────────────────────────────────
let structureActive        = false;
let structureMicroSeries   = null;   // Lila Micro-Pivot ZigZag
let structureH4Series      = null;   // Golden H4 Master Pivots (immer 1 Serie)

// Dynamische Series-Pools für Segmente mit Lücken (LWC v4 unterstützt kein null)
// Jeder Sub-Pfad bekommt eine eigene LineSeries.
let seriesPoolH4Temp      = [];   // Dashed Yellow H4 Temp
let seriesPoolH4Proj      = [];   // Dotted Yellow H4 Projected
let seriesPoolH1Inner     = [];   // Solid Green H1 Inner
let seriesPoolH1Proj      = [];   // Dotted Green H1 Projected
let seriesPoolM15Inner    = [];   // Solid Aqua M15 Inner
let seriesPoolM15Proj     = [];   // Dotted Aqua M15 Projected
let seriesPoolM5Inner     = [];   // Solid Orange M5 Inner
let seriesPoolM5Proj      = [];   // Dotted Orange M5 Projected
let seriesPoolM1Inner     = [];   // Solid Pink M1 Inner
let seriesPoolM1Proj      = [];   // Dotted Pink M1 Projected

/** Sichtbarkeit der einzelnen Struktur-Layer (persistiert über TF-Wechsel) */
const layerVisibility = { micro: true, h4_master: true, h1_inner: true, m15_inner: true, m5_inner: true, m1_inner: true };

/** Zuletzt geladene Struktur-Daten (für Layer-Toggles ohne Netzwerk-Request) */
let lastStructureData = null;

/**
 * Erstellt für jeden Sub-Pfad eine neue LWC LineSeries.
 * Optimiert: Reusability statt permanentes remove/add (Performance-Boost).
 */
function rebuildSeriesPool(pool, paths, opts) {
  const needed = (paths || []).length;

  // ⚠️ KRITISCH: isMutatingChart MUSS gesetzt sein bevor irgendeine Chart-Mutation stattfindet.
  // chart.addLineSeries() und series.setData() können intern subscribeVisibleTimeRangeChange
  // feuern, was handleScroll() aufrufen würde → Feedback-Loop!
  // Der Aufrufer (drawStructure / clearStructure) ist verantwortlich diesen Flag zu setzen.

  // 1. Alle im Pool befindlichen Serien vorerst verstecken und zurücksetzen
  pool.forEach(s => {
    s.setData([]);
    s.applyOptions({ visible: false });
  });

  if (needed === 0) return;

  const LineStyle = LightweightCharts.LineStyle;
  // Wir erzwingen visible: false in den Basis-Optionen, damit sie beim Erstellen/Update nicht kurz aufblitzen
  const baseOpts = { ...opts, visible: false };

  for (let i = 0; i < needed; i++) {
    const path = paths[i];
    if (!path || !Array.isArray(path) || path.length < 2) continue;

    // Deduplizieren: Map nach Timestamp (LWC v4 Pflicht)
    const map = new Map();
    path.forEach(p => { 
      if (p && p.time !== undefined) {
        map.set(p.time, { time: p.time, value: p.price });
      }
    });
    const data = Array.from(map.values()).sort((a, b) => a.time - b.time);
    if (data.length < 2) continue;

    let s;
    if (i < pool.length) {
      s = pool[i];
      s.applyOptions(baseOpts);
    } else {
      s = chart.addLineSeries(baseOpts);
      pool.push(s);
    }

    s.setData(data);
    // Hinweis: Die Sichtbarkeit wird danach explizit via setPoolVisibility gesteuert
  }
}

function setPoolVisibility(pool, visible) {
  pool.forEach(s => { try { s.applyOptions({ visible }); } catch(e) {} });
}

// ── Hilfsfunktionen ────────────────────────────────────────────────────────────

function tfToMinutes(tf) {
  return TF_MINUTES[tf] ?? 60;
}

/** Unix-Timestamp (Sekunden) des Bar-Starts für einen gegebenen Tick-Timestamp */
function getBarTime(unixSec, tfMinutes) {
  const barSec = tfMinutes * 60;
  return Math.floor(unixSec / barSec) * barSec;
}

function isoToUnix(isoStr) {
  return Math.floor(new Date(isoStr).getTime() / 1000);
}

function setConnectionState(state) {  // "connected" | "disconnected" | "connecting"
  const dot   = document.getElementById("connection-dot");
  const label = document.getElementById("connection-label");
  dot.className = state;
  const labels = { connected: "Live", disconnected: "Getrennt", connecting: "Verbinde …" };
  label.textContent = labels[state] ?? state;
}

function showError(msg) {
  const toast = document.getElementById("error-toast");
  toast.textContent = msg;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 5000);
}

function setLoading(on) {
  const overlay = document.getElementById("loading-overlay");
  if (on) overlay.classList.remove("hidden");
  else     overlay.classList.add("hidden");
}

function updatePriceDisplay(price, prevPrice) {
  const el = document.getElementById("price-display");
  el.textContent = price != null ? price.toFixed(price > 100 ? 2 : 5) : "—";
  if (prevPrice == null || price === prevPrice) {
    el.className = "";
  } else {
    el.className = price > prevPrice ? "up" : "down";
  }
}

// ── Chart initialisieren ───────────────────────────────────────────────────────

function initChart() {
  const container = document.getElementById("chart");

  chart = LightweightCharts.createChart(container, {
    layout: {
      background:  { color: "#131722" },
      textColor:   "#d1d4dc",
    },
    grid: {
      vertLines:   { color: "#1e222d" },
      horzLines:   { color: "#1e222d" },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
    },
    rightPriceScale: {
      borderColor: "#363a45",
    },
    timeScale: {
      borderColor:        "#363a45",
      timeVisible:        true,
      secondsVisible:     false,
      rightOffset:        8,
      barSpacing:         8,
      minBarSpacing:      3,
    },
    watermark: { visible: false },
  });

  candleSeries = chart.addCandlestickSeries({
    upColor:          "#26a69a",
    downColor:        "#ef5350",
    borderUpColor:    "#26a69a",
    borderDownColor:  "#ef5350",
    wickUpColor:      "#26a69a",
    wickDownColor:    "#ef5350",
  });

  volumeSeries = chart.addHistogramSeries({
    priceFormat:   { type: "volume" },
    priceScaleId:  "volume",
    color:         "#26a69a44",
  });
  chart.priceScale("volume").applyOptions({
    scaleMargins: { top: 0.8, bottom: 0 },
  });

  // Chart responsiv machen
  const resizeObs = new ResizeObserver(() => {
    chart.applyOptions({
      width:  container.clientWidth,
      height: container.clientHeight,
    });
  });
  resizeObs.observe(container);
  chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });

  // ── Scroll- & Viewport-Listener für Lazy Loading ──────────────────────────
  chart.timeScale().subscribeVisibleTimeRangeChange(() => {
    handleScroll();
  });

  // ── Struktur-Serien initialisieren (einmalig statt bei jedem Update) ───────
  const LineStyle = LightweightCharts.LineStyle;
  
  structureMicroSeries = chart.addLineSeries({
    color:                  "#d44bec",
    lineWidth:              1,
    priceLineVisible:       false,
    lastValueVisible:       false,
    crosshairMarkerVisible: false,
    lineStyle:              LineStyle.Solid,
    visible:                false, // Startet versteckt
  });

  structureH4Series = chart.addLineSeries({
    color:                  "#facc15",
    lineWidth:              3,
    priceLineVisible:       false,
    lastValueVisible:       false,
    crosshairMarkerVisible: false,
    lineStyle:              LineStyle.Solid,
    visible:                false,
  });
  // Alle anderen Struktur-Layer werden dynamisch per rebuildSeriesPool() verwaltet.
}
// ── Viewport & Scrolling ───────────────────────────────────────────────────────

/**
 * Reagiert auf Scrolling und Zooming.
 * Lädt Kerzen nach (Lazy Loading) und aktualisiert die Struktur (Viewport-Aware).
 */
function handleScroll() {
  // ⚠️ KRITISCH: isMutatingChart-Guard MUSS die erste Prüfung sein.
  // Verhindert die Feedback-Loop: drawStructure → addLineSeries → Event → handleScroll → drawStructure → ...
  if (isMutatingChart) return;
  if (!chart || isLoadingHistory || isLoadingStructure) return;

  const logicalRange = chart.timeScale().getVisibleLogicalRange();
  if (!logicalRange) return;

  const historyKey = `${currentSymbol}_${currentTF}`;

  // 1. Lazy Loading: Wenn wir nah am linken Rand sind (Index < 100), mehr Kerzen laden
  // Cooldown: Nur alle 1.5s auslösen
  const now = Date.now();
  if (logicalRange.from < 100 && !historyEndReached.has(historyKey) && (now - lastLoadTimestamp > 1500)) {
    console.log(`[Chart] Near left edge (from=${logicalRange.from.toFixed(2)}). Triggering loadHistory.`);
    lastLoadTimestamp = now;
    loadHistory(currentSymbol, currentTF, 1000, true);
  }

  // 2. Struktur-Update: Nur wenn nicht gerade geladen wird (Vermeidung von Scroll-Jumps)
  if (structureDebounceTimer) clearTimeout(structureDebounceTimer);
  structureDebounceTimer = setTimeout(() => {
    if (structureActive && !isLoadingHistory && !isLoadingStructure && !isMutatingChart) {
      loadStructure(currentSymbol, currentTF);
    }
  }, 400);
}

// ── Historische Daten laden ────────────────────────────────────────────────────

/**
 * Lädt historische Daten.
 * @param {string} symbol
 * @param {string} timeframe
 * @param {number} count
 * @param {boolean} isPrepend - Wenn true, werden die Daten VOR die existierenden gehängt.
 */
async function loadHistory(symbol, timeframe, count = 1000, isPrepend = false) {
  console.log(`[Chart] loadHistory(symbol=${symbol}, tf=${timeframe}, count=${count}, prepend=${isPrepend}) called`);
  if (isLoadingHistory) {
    console.warn("[Chart] loadHistory already in progress, aborting.");
    return;
  }
  isLoadingHistory = true;
  
  if (!isPrepend) setLoading(true);
  
  try {
    let url = `${API_BASE}/api/history?symbol=${symbol}&timeframe=${timeframe}&count=${count}`;
    
    // Cache-Buster & Logging
    url += `&_t=${Date.now()}`;

    // Offset-basierte Paginierung für stabiles MT5-Loading (Pagination in die Vergangenheit)
    if (isPrepend) {
      url += `&offset=${allCandles.length}`;
    }

    const resp = await fetch(url);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail ?? `HTTP ${resp.status}`);
    }

    const json = await resp.json();
    const newBars = json.candles;

    if (!newBars || newBars.length === 0) {
      if (!isPrepend) showError(`Keine Daten für ${symbol} ${timeframe}`);
      return;
    }

    // Merken der aktuellen Position für die Jump-Correction (erst JETZT direkt vor dem Update)
    let oldLogicalRange = null;
    if (isPrepend && chart) {
      oldLogicalRange = chart.timeScale().getVisibleLogicalRange();
    }

    if (isPrepend) {
      const oldLen = allCandles.length;
      // Defensive: Nur valide Kerzen mergen
      const combined = [...newBars, ...allCandles].filter(b => b && b.time != null);
      const uniqueMap = new Map();
      combined.forEach(b => uniqueMap.set(b.time, b));
      allCandles = Array.from(uniqueMap.values()).sort((a, b) => a.time - b.time);
      const newLen = allCandles.length;
      const actualAdded = newLen - oldLen;

      console.log(`[Chart] Prepend finished. New bars: ${newBars.length}, Unique added: ${actualAdded}`);

      // Wenn keine neuen Kerzen kamen, markieren wir das Ende um Endlosschleifen zu vermeiden
      if (actualAdded === 0) {
        console.warn("[Chart] No unique bars added. Reaching end of history.");
        historyEndReached.add(`${symbol}_${timeframe}`);
      }

      // Jump-Correction: Viewport stabil halten
      if (actualAdded > 0 && oldLogicalRange && chart) {
        chart.timeScale().setVisibleLogicalRange({
          from: oldLogicalRange.from + actualAdded,
          to:   oldLogicalRange.to + actualAdded,
        });
      }
    } else {
      allCandles = newBars;
    }

    // Daten im Chart aktualisieren
    candleSeries.setData(allCandles);

    const volData = allCandles
      .filter(b => b && b.time != null)
      .map(b => ({
        time:  b.time,
        value: b.volume || 0,
        color: (b.close >= b.open) ? "#26a69a44" : "#ef535044",
      }));
    volumeSeries.setData(volData);

    if (!isPrepend) {
      // Letzten Preis initialisieren
      if (allCandles.length > 0) {
        const last = allCandles[allCandles.length - 1];
        lastPrice = last.close;
        updatePriceDisplay(lastPrice, null);
        liveCandle = { ...last };
      }
      
      await refreshActiveIndicators(symbol, timeframe, count);
      // Struktur IMMER laden bei Initial-Load (Micro Pivots etc.)
      await loadStructure(symbol, timeframe);
    }

  } catch (err) {
    console.error("[Chart] loadHistory error:", err);
  } finally {
    // Timeout verhindert Feedback-Loops durch Scroll-Events nach Jump-Correction.
    // Wir geben der Chart-Engine hier 250ms (etwas mehr als zuvor), um sich zu stabilisieren.
    setTimeout(() => {
      isLoadingHistory = false;
      setLoading(false);
      console.log("[Chart] loadHistory lock released.");
    }, 250);
  }
}

// ── WebSocket – Live-Ticks ─────────────────────────────────────────────────────

function connectWebSocket(symbol) {
  if (wsConnection) {
    wsConnection.onclose = null;
    wsConnection.close();
    wsConnection = null;
  }
  if (wsReconnectTimer) {
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = null;
  }

  setConnectionState("connecting");
  const ws = new WebSocket(`${WS_BASE}/ws/live?symbol=${symbol}`);
  wsConnection = ws;

  ws.onopen    = () => { setConnectionState("connected"); };
  ws.onerror   = (err) => { setConnectionState("disconnected"); };
  ws.onmessage = (event) => {
    try { handleWsMessage(JSON.parse(event.data)); } catch (e) {}
  };
  ws.onclose   = () => {
    setConnectionState("disconnected");
    wsReconnectTimer = setTimeout(() => connectWebSocket(currentSymbol), 3000);
  };
}

function handleWsMessage(msg) {
  if (msg.type !== "tick") return;
  const price   = msg.mid ?? msg.bid;
  const tickTs  = isoToUnix(msg.time);
  const barTime = getBarTime(tickTs, tfToMinutes(currentTF));

  updatePriceDisplay(price, lastPrice);
  lastPrice = price;

  if (!liveCandle || liveCandle.time !== barTime) {
    liveCandle = { time: barTime, open: price, high: price, low: price, close: price };
  } else {
    liveCandle.high  = Math.max(liveCandle.high, price);
    liveCandle.low   = Math.min(liveCandle.low, price);
    liveCandle.close = price;
  }
  candleSeries.update(liveCandle);
}

async function switchSymbol(symbol) {
  if (symbol === currentSymbol) return;
  historyEndReached.clear(); // Reset bei Symbol-Wechsel
  currentSymbol = symbol;
  clearAllIndicators();
  clearStructure();
  allCandles = []; 
  await loadHistory(symbol, currentTF);
  connectWebSocket(symbol);
}

async function switchTF(tf) {
  console.log(`[Chart] switchTF(tf=${tf}) called. Current: ${currentTF}`);
  if (tf === currentTF) {
    console.log("[Chart] TF already active, ignoring.");
    return;
  }
  
  // TF-Sync: Zeitpunkt in der Bildschirmmitte merken
  let centerTime = null;
  try {
    const logicalRange = chart.timeScale().getVisibleLogicalRange();
    if (logicalRange && logicalRange.from !== null && logicalRange.to !== null && allCandles.length > 0) {
      const centerIndex = Math.round((logicalRange.from + logicalRange.to) / 2);
      const safeIndex = Math.max(0, Math.min(centerIndex, allCandles.length - 1));
      centerTime = allCandles[safeIndex].time;
    }
  } catch (err) {
    console.warn("[Chart] TF-Sync read failed:", err);
  }

  currentTF = tf;
  window.currentTF = tf;

  try {
    // Aktiven TF-Button hervorheben
    document.querySelectorAll(".tf-btn").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.tf === tf);
    });

    // Alles leeren für sauberen Wechsel
    historyEndReached.clear(); // Reset bei TF-Wechsel
    clearAllIndicators();
    resetActiveIndicatorButtons();
    clearStructure();
    
    await loadHistory(currentSymbol, tf);
    
    // TF-Sync: Nach dem Laden wieder auf den gemerkten Zeitpunkt zentrieren
    if (centerTime && chart && allCandles.length > 0) {
      setTimeout(() => {
        try {
          // Nächste Kerze zu centerTime finden
          let closestIndex = 0;
          let minDiff = Infinity;
          for (let i = 0; i < allCandles.length; i++) {
            const diff = Math.abs(allCandles[i].time - centerTime);
            if (diff < minDiff) {
              minDiff = diff;
              closestIndex = i;
            }
          }

          const width = document.getElementById("chart").clientWidth;
          let barSpacing = 6;
          try {
            // LWC v4 Standard-Zugriff laut LOGIC.md
            barSpacing = chart.options().timeScale.barSpacing || 6;
          } catch (e) {
            console.warn("[Chart] Failed to read barSpacing from options, using default.");
          }
          
          const barsVisible = width / barSpacing;
          const halfRange = barsVisible / 2;

          chart.timeScale().setVisibleLogicalRange({
            from: closestIndex - halfRange,
            to:   closestIndex + halfRange
          });
        } catch (err) {
          console.warn("[Chart] TF-Sync restore failed:", err);
        }
      }, 100);
    }
  } catch (err) {
    console.error("[Chart] Error in switchTF execution:", err);
  }
}

// ── Symbol-Selector befüllen ───────────────────────────────────────────────────

async function loadSymbols() {
  try {
    const resp = await fetch(`${API_BASE}/api/symbols`);
    if (!resp.ok) return;
    const json = await resp.json();

    const select = document.getElementById("symbol-select");
    select.innerHTML = "";

    // Das Standard-Symbol vom Server oder unser lokales currentSymbol nutzen
    const defaultSymbol = json.default || currentSymbol;

    for (const sym of json.symbols) {
      const opt = document.createElement("option");
      opt.value = sym;
      opt.textContent = sym;
      
      // Smarter Match: Exakt oder wenn sym mit currentSymbol beginnt (z.B. XAUUSD.s)
      const isMatch = (sym === currentSymbol) || 
                      (sym.startsWith(currentSymbol + ".") && sym.length <= currentSymbol.length + 3);

      if (isMatch) {
        opt.selected = true;
        currentSymbol = sym; // Update auf den echten Broker-Namen
      }
      
      select.appendChild(opt);
    }
    
    // Falls nach dem Loop nichts selektiert ist, nehmen wir das erste
    if (select.selectedIndex === -1 && select.options.length > 0) {
        select.options[0].selected = true;
        currentSymbol = select.options[0].value;
    }
  } catch (e) {
    console.warn("Symbole konnten nicht geladen werden:", e);
    // Fallback: manuell tippen lassen
  }
}

// ── Indikator-Linien ───────────────────────────────────────────────────────────

/**
 * Zeichnet eine benannte Linie über den Chart.
 *
 * @param {string} name       - Eindeutiger Name (z.B. "SMA 50")
 * @param {Array}  data       - [{time: unix_int, value: float}, ...]
 * @param {string} color      - CSS-Farbe, z.B. "#f59e0b"
 * @param {number} lineWidth  - Strichbreite (default: 1.5)
 */
function drawIndicatorLine(name, data, color = "#f59e0b", lineWidth = 1.5) {
  // Bestehende Linie entfernen falls vorhanden
  if (indicatorSeries.has(name)) {
    chart.removeSeries(indicatorSeries.get(name));
  }

  const series = chart.addLineSeries({
    color,
    lineWidth,
    priceLineVisible: false,
    lastValueVisible: true,
    crosshairMarkerVisible: false,
  });
  series.setData(data);

  indicatorSeries.set(name, series);
  indicatorMeta.set(name, { color, lineWidth });
  updateLegend();
}

function removeIndicatorLine(name) {
  if (indicatorSeries.has(name)) {
    chart.removeSeries(indicatorSeries.get(name));
    indicatorSeries.delete(name);
    indicatorMeta.delete(name);
    updateLegend();
  }
}

function clearAllIndicators() {
  for (const [, series] of indicatorSeries) {
    chart.removeSeries(series);
  }
  indicatorSeries.clear();
  indicatorMeta.clear();
  updateLegend();
}

// ── Indikator vom Backend laden ────────────────────────────────────────────────

const INDICATOR_COLORS = {
  "SMA 50":  "#f59e0b",
  "SMA 200": "#a78bfa",
  "EMA 20":  "#34d399",
  "RSI 14":  "#60a5fa",
};

/** Welche Indikatoren gerade aktiv sind (Set von Labels wie "SMA 50") */
const activeIndicators = new Set();

async function toggleIndicator(label, indName, period) {
  const btn = document.querySelector(`[data-ind="${label}"]`);

  if (activeIndicators.has(label)) {
    // Deaktivieren
    activeIndicators.delete(label);
    removeIndicatorLine(label);
    if (btn) btn.classList.remove("active");
    return;
  }

  // Aktivieren – vom Backend laden
  activeIndicators.add(label);
  if (btn) btn.classList.add("active");

  try {
    const url = `${API_BASE}/api/indicator?name=${indName}&period=${period}&symbol=${currentSymbol}&timeframe=${currentTF}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const json = await resp.json();

    const color = INDICATOR_COLORS[label] ?? "#888";
    drawIndicatorLine(label, json.data, color);
  } catch (e) {
    console.error(`Indikator ${label} Fehler:`, e);
    showError(`${label} konnte nicht geladen werden: ${e.message}`);
    activeIndicators.delete(label);
    if (btn) btn.classList.remove("active");
  }
}

async function refreshActiveIndicators(symbol, timeframe, count) {
  if (activeIndicators.size === 0) return;

  const INDICATOR_PARAMS = {
    "SMA 50":  { name: "SMA",  period: 50  },
    "SMA 200": { name: "SMA",  period: 200 },
    "EMA 20":  { name: "EMA",  period: 20  },
    "RSI 14":  { name: "RSI",  period: 14  },
  };

  for (const label of activeIndicators) {
    const params = INDICATOR_PARAMS[label];
    if (!params) continue;
    try {
      const url = `${API_BASE}/api/indicator?name=${params.name}&period=${params.period}&symbol=${symbol}&timeframe=${timeframe}&count=${count}`;
      const resp = await fetch(url);
      if (!resp.ok) continue;
      const json = await resp.json();
      const color = INDICATOR_COLORS[label] ?? "#888";
      drawIndicatorLine(label, json.data, color);
    } catch (e) {
      console.warn(`Indikator ${label} refresh fehlgeschlagen:`, e);
    }
  }
}

function resetActiveIndicatorButtons() {
  document.querySelectorAll(".ind-btn").forEach(btn => btn.classList.remove("active"));
  activeIndicators.clear();
}

// ── Legende ───────────────────────────────────────────────────────────────────

function updateLegend() {
  const legend = document.getElementById("indicator-legend");
  legend.innerHTML = "";
  for (const [name, meta] of indicatorMeta) {
    const item = document.createElement("div");
    item.className = "legend-item";
    item.innerHTML = `
      <span class="legend-dot" style="background:${meta.color}"></span>
      <span>${name}</span>
    `;
    legend.appendChild(item);
  }
}

// =============================================================================
// STRUKTUR-OVERLAY
// =============================================================================

/**
 * Konvertiert ein PivotPoint-Dict (vom Backend) in einen Lightweight Charts
 * Datenpunkt: { time: unix_int, value: float }
 */
function pivotToPoint(p) {
  return { time: p.time, value: p.price };
}

/**
 * Gibt true zurück, wenn der aktuelle Timeframe für H1-Struktur geeignet ist (<= 1h).
 */
function isH1Eligible(tf) {
  return tfToMinutes(tf) <= 60;
}

/**
 * Gibt true zurück, wenn der aktuelle Timeframe für M15-Struktur geeignet ist (<= 15m).
 */
function isM15Eligible(tf) {
  return tfToMinutes(tf) <= 15;
}

/**
 * Gibt true zurück, wenn der aktuelle Timeframe für M5-Struktur geeignet ist (<= 5m).
 */
function isM5Eligible(tf) {
  return tfToMinutes(tf) <= 5;
}

/**
 * Gibt true zurück, wenn der aktuelle Timeframe für M1-Struktur geeignet ist (<= 1m).
 */
function isM1Eligible(tf) {
  return tfToMinutes(tf) <= 1;
}

/**
 * Entfernt alle Struktur-Overlays aus dem Chart.
 */
function clearStructure() {
  isMutatingChart = true;
  // Viewport vor der Mutation sichern, damit setData() keinen Sprung verursacht.
  const _savedRange = chart ? chart.timeScale().getVisibleLogicalRange() : null;
  try {
    if (structureMicroSeries)   structureMicroSeries.setData([]);
    if (structureH4Series)      structureH4Series.setData([]);
    // Dynamische Pools leeren
    rebuildSeriesPool(seriesPoolH4Temp,   [], {});
    rebuildSeriesPool(seriesPoolH4Proj,   [], {});
    rebuildSeriesPool(seriesPoolH1Inner,  [], {});
    rebuildSeriesPool(seriesPoolH1Proj,   [], {});
    rebuildSeriesPool(seriesPoolM15Inner, [], {});
    rebuildSeriesPool(seriesPoolM15Proj,  [], {});
    rebuildSeriesPool(seriesPoolM5Inner,  [], {});
    rebuildSeriesPool(seriesPoolM5Proj,   [], {});
    rebuildSeriesPool(seriesPoolM1Inner,  [], {});
    rebuildSeriesPool(seriesPoolM1Proj,   [], {});
    updateTrendBadge(null);
  } finally {
    // Kurze Verzögerung: LWC feuert Events asynchron nach setData/addLineSeries.
    // Viewport wird wiederhergestellt um den durch setData() verursachten Micro-Sprung zu verhindern.
    setTimeout(() => {
      if (_savedRange && chart) {
        chart.timeScale().setVisibleLogicalRange(_savedRange);
      }
      isMutatingChart = false;
    }, 50);
  }
}

/**
 * Zeichnet die Struktur-Overlays basierend auf den Daten von /api/structure.
 * @param {Object} data - Response von /api/structure
 */
function drawStructure(data) {
  if (!data) return;
  lastStructureData = data;

  // ⚠️ KRITISCH: isMutatingChart-Flag während aller Chart-Mutationen setzen.
  // Verhindert die Feedback-Loop durch subscribeVisibleTimeRangeChange.
  isMutatingChart = true;
  // Viewport vor der Mutation sichern, damit setData()/addLineSeries() keinen Sprung verursacht.
  const _savedRange = chart ? chart.timeScale().getVisibleLogicalRange() : null;

  const LineStyle = LightweightCharts.LineStyle;

  // 1. Lila Micro-Pivots
  if (structureMicroSeries) {
    if (structureActive && data.micro_pivots && data.micro_pivots.length >= 2) {
      const microMap = new Map();
      for (const p of data.micro_pivots) {
        if (p && p.time != null && p.price != null) {
          microMap.set(p.time, { time: p.time, value: p.price });
        }
      }
      const microData = Array.from(microMap.values()).sort((a, b) => a.time - b.time);
      if (microData.length >= 2) {
        structureMicroSeries.setData(microData);
        structureMicroSeries.applyOptions({ visible: layerVisibility.micro });
      } else {
        structureMicroSeries.setData([]);
      }
    } else {
      structureMicroSeries.setData([]);
      structureMicroSeries.applyOptions({ visible: false });
    }
  }

  // 2. H4 Master
  if (structureH4Series) {
    if (structureActive && data.h4_master_pivots && data.h4_master_pivots.length >= 2) {
      const h4Data = data.h4_master_pivots
        .filter(p => p && p.time != null && p.price != null)
        .map(p => ({ time: p.time, value: p.price }));
      
      if (h4Data.length >= 2) {
        structureH4Series.setData(h4Data);
        structureH4Series.applyOptions({ visible: layerVisibility.h4_master });
      } else {
        structureH4Series.setData([]);
      }
    } else {
      structureH4Series.setData([]);
      structureH4Series.applyOptions({ visible: false });
    }
  }

  // 3. Pools (H4, H1, M15, M5, M1)
  const showH4 = structureActive && layerVisibility.h4_master;
  rebuildSeriesPool(seriesPoolH4Temp, data.h4_temp_pivots, {
    color: '#facc15', lineWidth: 2, lineStyle: LineStyle.Dashed,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false
  });
  setPoolVisibility(seriesPoolH4Temp, showH4);

  rebuildSeriesPool(seriesPoolH4Proj, data.h4_projected_pivots, {
    color: '#facc15', lineWidth: 2, lineStyle: LineStyle.Dotted,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false
  });
  setPoolVisibility(seriesPoolH4Proj, showH4);

  const h1Eligible = isH1Eligible(currentTF);
  const showH1 = structureActive && h1Eligible && layerVisibility.h1_inner;
  rebuildSeriesPool(seriesPoolH1Inner, data.h1_inner_structure, {
    color: '#4ade80', lineWidth: 1, lineStyle: LineStyle.Solid,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false
  });
  setPoolVisibility(seriesPoolH1Inner, showH1);

  rebuildSeriesPool(seriesPoolH1Proj, data.h1_projected_pivots, {
    color: '#4ade80', lineWidth: 1, lineStyle: LineStyle.Dotted,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false
  });
  setPoolVisibility(seriesPoolH1Proj, showH1);

  const m15Eligible = isM15Eligible(currentTF);
  const showM15 = structureActive && m15Eligible && layerVisibility.m15_inner;
  rebuildSeriesPool(seriesPoolM15Inner, data.m15_inner_structure, {
    color: '#00e5ff', lineWidth: 1, lineStyle: LineStyle.Solid,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false
  });
  setPoolVisibility(seriesPoolM15Inner, showM15);

  rebuildSeriesPool(seriesPoolM15Proj, data.m15_projected_pivots, {
    color: '#00e5ff', lineWidth: 1, lineStyle: LineStyle.Dotted,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false
  });
  setPoolVisibility(seriesPoolM15Proj, showM15);

  const m5Eligible = isM5Eligible(currentTF);
  const showM5 = structureActive && m5Eligible && layerVisibility.m5_inner;
  rebuildSeriesPool(seriesPoolM5Inner, data.m5_inner_structure, {
    color: '#f97316', lineWidth: 1, lineStyle: LineStyle.Solid,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false
  });
  setPoolVisibility(seriesPoolM5Inner, showM5);

  rebuildSeriesPool(seriesPoolM5Proj, data.m5_projected_pivots, {
    color: '#f97316', lineWidth: 1, lineStyle: LineStyle.Dotted,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false
  });
  setPoolVisibility(seriesPoolM5Proj, showM5);

  const m1Eligible = isM1Eligible(currentTF);
  const showM1 = structureActive && m1Eligible && layerVisibility.m1_inner;
  rebuildSeriesPool(seriesPoolM1Inner, data.m1_inner_structure, {
    color: '#ec4899', lineWidth: 1, lineStyle: LineStyle.Solid,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false
  });
  setPoolVisibility(seriesPoolM1Inner, showM1);

  rebuildSeriesPool(seriesPoolM1Proj, data.m1_projected_pivots, {
    color: '#ec4899', lineWidth: 1, lineStyle: LineStyle.Dotted,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false
  });
  setPoolVisibility(seriesPoolM1Proj, showM1);

  updateTrendBadge(data);

  // Mutation abgeschlossen – Viewport wiederherstellen und Flag zurücksetzen.
  // Die 50ms-Verzögerung gibt LWC Zeit, interne Events zu feuern (subscribeVisibleTimeRangeChange)
  // bevor der Guard aufgehoben wird. Die Viewport-Wiederherstellung verhindert den
  // durch setData()/addLineSeries() verursachten Micro-Sprung nach links.
  setTimeout(() => {
    if (_savedRange && chart) {
      chart.timeScale().setVisibleLogicalRange(_savedRange);
    }
    isMutatingChart = false;
  }, 50);
}

/**
 * Schaltet einen einzelnen Struktur-Layer ein oder aus.
 * Wird von den Layer-Toggle-Buttons im Topbar aufgerufen.
 */
function toggleStructureLayer(btn) {
  const layer = btn.dataset.layer;
  layerVisibility[layer] = !layerVisibility[layer];
  btn.classList.toggle("active", layerVisibility[layer]);

  if (!structureActive || !lastStructureData) return;

  switch (layer) {
    case "micro":
      if (structureMicroSeries)
        structureMicroSeries.applyOptions({ visible: layerVisibility.micro });
      break;

    case "h4_master":
      if (structureH4Series)
        structureH4Series.applyOptions({ visible: layerVisibility.h4_master });
      setPoolVisibility(seriesPoolH4Temp, layerVisibility.h4_master);
      setPoolVisibility(seriesPoolH4Proj, layerVisibility.h4_master);
      break;

    case "h1_inner": {
      const v = layerVisibility.h1_inner && isH1Eligible(currentTF);
      setPoolVisibility(seriesPoolH1Inner, v);
      setPoolVisibility(seriesPoolH1Proj,  v);
      break;
    }

    case "m15_inner": {
      const v = layerVisibility.m15_inner && isM15Eligible(currentTF);
      setPoolVisibility(seriesPoolM15Inner, v);
      setPoolVisibility(seriesPoolM15Proj,  v);
      break;
    }

    case "m5_inner": {
      const v = layerVisibility.m5_inner && isM5Eligible(currentTF);
      setPoolVisibility(seriesPoolM5Inner, v);
      setPoolVisibility(seriesPoolM5Proj,  v);
      break;
    }

    case "m1_inner": {
      const v = layerVisibility.m1_inner && isM1Eligible(currentTF);
      setPoolVisibility(seriesPoolM1Inner, v);
      setPoolVisibility(seriesPoolM1Proj,  v);
      break;
    }
  }
}

function updateTrendBadge(data) {
  const badge = document.getElementById("trend-badge");
  if (!badge) return;

  if (!data || (!data.h4_trend && !data.h4_potential_trend)) {
    badge.className = "trend-badge hidden";
    return;
  }

  badge.className = "trend-badge visible";
  
  const h4Trend     = data.h4_trend || "Neutral";
  const potTrend    = data.h4_potential_trend || h4Trend;
  
  const h4Class     = h4Trend.toLowerCase();
  const potClass    = potTrend.toLowerCase();
  
  badge.innerHTML = `
    <div class="badge-row">
      <span class="badge-label">H4 Master:</span>
      <span class="badge-value ${h4Class}">${h4Trend}</span>
    </div>
    <div class="badge-row">
      <span class="badge-label">Potential:</span>
      <span class="badge-value ${potClass}">${potTrend}</span>
    </div>
  `;
}

let isLoadingStructure = false;

/**
 * Lädt Struktur-Daten vom Backend und zeichnet sie.
 */
async function loadStructure(symbol, timeframe, count = 1000) {
  if (isLoadingStructure || isLoadingHistory) return;
  if (!structureActive) return;

  try {
    let url = `${API_BASE}/api/structure?symbol=${symbol}&timeframe=${timeframe}&count=${count}&pivot_length=${currentPivotLength}`;
    
    if (chart && allCandles && allCandles.length > 0) {
      const logicalRange = chart.timeScale().getVisibleLogicalRange();
      if (logicalRange && logicalRange.from !== null && logicalRange.to !== null) {
        let idxFrom = Math.floor(logicalRange.from);
        let idxTo   = Math.ceil(logicalRange.to);

        // Schutz gegen NaN oder Race-Conditions
        if (isNaN(idxFrom) || isNaN(idxTo)) {
          console.warn("[Structure] Viewport indices are NaN. Skipping update.");
          return;
        }

        const maxIdx = allCandles.length - 1;
        idxFrom = Math.max(0, Math.min(maxIdx, idxFrom));
        idxTo   = Math.max(0, Math.min(maxIdx, idxTo));
        
        const cFrom = allCandles[idxFrom];
        const cTo   = allCandles[idxTo];

        if (cFrom && cTo && typeof cFrom.time !== 'undefined' && typeof cTo.time !== 'undefined') {
          url += `&viewport_start=${cFrom.time}&viewport_end=${cTo.time}`;
        }
      }
    }
    
    isLoadingStructure = true;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    
    const data = await resp.json();
    drawStructure(data);
  } catch (e) {
    console.error("[Structure] loadStructure error:", e);
  } finally {
    isLoadingStructure = false;
  }
}

/**
 * Struktur-Button Toggle-Handler.
 */
async function toggleStructure() {
  console.log("[Chart] toggleStructure() called. Active before:", structureActive);
  const btn        = document.getElementById("structure-btn");
  const layerPanel = document.getElementById("layer-buttons");
  const pivotBox   = document.getElementById("pivot-control-box");
  structureActive  = !structureActive;

  if (structureActive) {
    btn.classList.add("active");
    if (layerPanel) layerPanel.classList.remove("hidden");
    if (pivotBox)   pivotBox.classList.remove("hidden");
    console.log("[Chart] Loading structure...");
    await loadStructure(currentSymbol, currentTF);
  } else {
    btn.classList.remove("active");
    if (layerPanel) layerPanel.classList.add("hidden");
    if (pivotBox)   pivotBox.classList.add("hidden");
    console.log("[Chart] Clearing structure.");
    clearStructure();
  }
}

// ── App starten ────────────────────────────────────────────────────────────────

async function initApp() {
  // 1. UI Event Listeners sofort registrieren (vor dem Await-Laden)
  // Struktur-Button
  const strBtn = document.getElementById("structure-btn");
  if (strBtn) strBtn.addEventListener("click", toggleStructure);

  // TF-Buttons Events
  document.querySelectorAll(".tf-btn").forEach(btn => {
    btn.addEventListener("click", () => switchTF(btn.dataset.tf));
  });

  // Layer-Toggle Buttons
  document.querySelectorAll(".layer-btn").forEach(btn => {
    btn.addEventListener("click", () => toggleStructureLayer(btn));
  });

  // Symbol-Selector
  const select = document.getElementById("symbol-select");
  if (select) select.addEventListener("change", () => switchSymbol(select.value));

  // Pivot-Slider listener
  const pivotSlider = document.getElementById("pivot-slider");
  const pivotDisplay = document.getElementById("pivot-val-display");
  if (pivotSlider && pivotDisplay) {
    pivotSlider.addEventListener("input", (e) => {
      currentPivotLength = parseInt(e.target.value);
      pivotDisplay.textContent = currentPivotLength;
    });
    pivotSlider.addEventListener("change", () => {
      if (structureActive) loadStructure(currentSymbol, currentTF);
    });
  }

  // 2. Chart & Daten initialisieren
  initChart();
  
  // Historische Daten laden
  await loadHistory(currentSymbol, currentTF);

  // WebSocket verbinden
  connectWebSocket(currentSymbol);

  // Symbol-Selector initialisieren (Broker-Liste laden)
  await loadSymbols();
}

// Starten wenn DOM fertig
document.addEventListener("DOMContentLoaded", initApp);

/**
 * Schaltet das Volumen-Histogramm an/aus.
 */
function toggleVolume() {
  if (!volumeSeries) return;
  const btn = document.getElementById("volume-toggle-btn");
  const isVisible = volumeSeries.options().visible;
  
  const nextVisible = !isVisible;
  volumeSeries.applyOptions({ visible: nextVisible });
  
  if (btn) {
    btn.classList.toggle("active", nextVisible);
  }
}
