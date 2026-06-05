/**
 * chart.js – Trading Dashboard (Clean Rebuild)
 *
 * Was bleibt:
 *   - Candlestick + Volume laden (History + Lazy-Load)
 *   - WebSocket Live-Ticks
 *   - Symbol- und Timeframe-Wechsel
 *   - Indikatoren (SMA/EMA)
 *
 * Neu (Bottom-Up Struktur, frisch aufgebaut):
 *   - Level 1 (Cyan)  – erste bestätigte Struktur-Ebene
 *   - Level 2 (Orange) – zweite bestätigte Ebene
 *   - Je eine Serie für "confirmed" (solid) und "temp" (dashed)
 *   - Jedes Level einzeln an/aus schaltbar
 *
 * Alles andere (Top-Down H4, M1/M5/M15/H1-Layer, Series-Pools) ist
 * vollständig entfernt.
 */

"use strict";

// ══════════════════════════════════════════════════════════════════════════════
// Konfiguration
// ══════════════════════════════════════════════════════════════════════════════

const API_BASE = "";
const WS_PROTO = location.protocol === "https:" ? "wss:" : "ws:";
const WS_BASE  = `${WS_PROTO}//${location.host}`;

/** Minuten pro Timeframe – für Live-Bar-Berechnung */
const TF_MINUTES = { "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440 };

// ══════════════════════════════════════════════════════════════════════════════
// Globaler Zustand
// ══════════════════════════════════════════════════════════════════════════════

let chart        = null;   // LWC IChartApi
let candleSeries = null;   // Candlestick-Serie
let volumeSeries = null;   // Volumen-Histogramm
let wsConn       = null;   // aktive WebSocket-Verbindung
let wsRetryTimer = null;

let currentSymbol = "XAUUSD";
let currentTF     = "5m";
window.currentTF  = currentTF;   // Export für inline-HTML

let liveCandle = null;   // offene, noch nicht abgeschlossene Kerze
let lastPrice  = 0;

// History-Lade-State
let allCandles       = [];          // Cache aller Kerzen (für Prepend + Sync)
let isLoadingHistory = false;
let lastHistoryLoad  = 0;           // Cooldown-Timestamp
let historyExhausted = new Set();   // "SYMBOL_TF" wenn keine weiteren Kerzen

// Verhindert Feedback-Loop bei Chart-Mutationen (siehe unten)
let isMutating = false;

// Struktur
let structureActive       = false;
let isLoadingStructure    = false;
let structureDebounce     = null;
let currentPivotLength    = 2;
let lastStructureData     = null;

// Sichtbarkeit der einzelnen Level-Serien
// true = Nutzer möchte diese Serie sehen (wenn Struktur aktiv)
const levelVisible = { l1: true, l1t: true, l2: true, l2t: true };

// ══════════════════════════════════════════════════════════════════════════════
// Struktur-Serien  (Bottom-Up, Level 1 + 2)
// ══════════════════════════════════════════════════════════════════════════════

let l1Series  = null;   // Level 1 bestätigt  (Cyan, solid)
let l1tSeries = null;   // Level 1 temp       (Cyan, dashed)
let l2Series  = null;   // Level 2 bestätigt  (Orange, solid)
let l2tSeries = null;   // Level 2 temp       (Orange, dashed)

// Mapping: data-series-Schlüssel → Serie
function seriesMap() {
  return { l1: l1Series, l1t: l1tSeries, l2: l2Series, l2t: l2tSeries };
}

// Indikator-Serien
const indicatorSeries = new Map();   // label → LineSeries
const indicatorMeta   = new Map();   // label → { color }
const activeIndicators = new Set();  // aktive Label

// ══════════════════════════════════════════════════════════════════════════════
// Hilfsfunktionen
// ══════════════════════════════════════════════════════════════════════════════

function tfToMin(tf) { return TF_MINUTES[tf] ?? 60; }

/** Rundet einen Unix-Timestamp auf den Kerzen-Beginn des Timeframes ab */
function barTime(unixSec, tfMin) {
  const step = tfMin * 60;
  return Math.floor(unixSec / step) * step;
}

function isoToUnix(iso) { return Math.floor(new Date(iso).getTime() / 1000); }

function setConnectionState(state) {
  const dot   = document.getElementById("connection-dot");
  const label = document.getElementById("connection-label");
  if (!dot || !label) return;
  dot.className = state;
  label.textContent = { connected: "Live", disconnected: "Getrennt", connecting: "Verbinde …" }[state] ?? state;
}

function showError(msg) {
  const t = document.getElementById("error-toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 5000);
}

function setLoading(on) {
  const el = document.getElementById("loading-overlay");
  if (!el) return;
  el.classList.toggle("hidden", !on);
}

function updatePriceDisplay(price, prev) {
  const el = document.getElementById("price-display");
  if (!el) return;
  el.textContent = price != null ? price.toFixed(price > 100 ? 2 : 5) : "—";
  if (prev == null || price === prev) el.className = "";
  else el.className = price > prev ? "up" : "down";
}

/**
 * Baut aus einem Pivot-Array (Backend-Format: [{time, price}, ...])
 * ein sauberes LWC-Array ({time, value}) und entfernt Duplikate.
 */
function pivotToLwcData(pivots) {
  if (!pivots || pivots.length < 1) return [];
  const map = new Map();
  pivots.forEach(p => {
    if (p && p.time != null && p.price != null) {
      map.set(p.time, { time: p.time, value: p.price });
    }
  });
  return Array.from(map.values()).sort((a, b) => a.time - b.time);
}

// ══════════════════════════════════════════════════════════════════════════════
// Chart initialisieren
// ══════════════════════════════════════════════════════════════════════════════

function initChart() {
  const container = document.getElementById("chart");
  const LS = LightweightCharts.LineStyle;

  // Basis-Chart
  chart = LightweightCharts.createChart(container, {
    layout:          { background: { color: "#131722" }, textColor: "#d1d4dc" },
    grid:            { vertLines: { color: "#1e222d" }, horzLines: { color: "#1e222d" } },
    crosshair:       { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "#363a45" },
    timeScale: {
      borderColor:     "#363a45",
      timeVisible:     true,
      secondsVisible:  false,
      rightOffset:     8,
      barSpacing:      8,
      minBarSpacing:   3,
    },
    watermark: { visible: false },
  });

  // Kerzen
  candleSeries = chart.addCandlestickSeries({
    upColor:        "#26a69a", downColor:        "#ef5350",
    borderUpColor:  "#26a69a", borderDownColor:  "#ef5350",
    wickUpColor:    "#26a69a", wickDownColor:    "#ef5350",
  });

  // Volumen
  volumeSeries = chart.addHistogramSeries({
    priceFormat:  { type: "volume" },
    priceScaleId: "volume",
    color:        "#26a69a44",
  });
  chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

  // Responsiv
  new ResizeObserver(() =>
    chart.applyOptions({ width: container.clientWidth, height: container.clientHeight })
  ).observe(container);
  chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });

  // Scroll-Handler (Lazy-Load + Struktur-Debounce)
  chart.timeScale().subscribeVisibleTimeRangeChange(handleScroll);

  // ── Bottom-Up Struktur-Serien ─────────────────────────────────────────────
  //
  // ⚠️ WICHTIG: priceScaleId: "right" erzwingen!
  //   Ohne diese Option legt LWC jede LineSeries auf eine EIGENE unsichtbare
  //   Price-Scale, die einen extra Zeit-Slot reserviert → Lücken zwischen
  //   Candles, sobald Struktur-Daten vorhanden sind.
  //   Durch priceScaleId: "right" teilen sich alle Serien dieselbe Skala.
  //
  // ⚠️ visible: false beim Erstellen, damit handleScroll() nicht sofort feuert
  //   bevor Candle-Daten geladen sind.
  //
  const seriesOpts = {
    priceScaleId:           "right",   // ← FIX: teilt sich die Candle-Price-Scale
    priceLineVisible:       false,
    lastValueVisible:       false,
    crosshairMarkerVisible: false,
    visible:                false,
  };

  l1Series  = chart.addLineSeries({ ...seriesOpts, color: "#00e5ff", lineWidth: 1, lineStyle: LS.Solid  });
  l1tSeries = chart.addLineSeries({ ...seriesOpts, color: "#00e5ff", lineWidth: 1, lineStyle: LS.Dashed });
  l2Series  = chart.addLineSeries({ ...seriesOpts, color: "#f97316", lineWidth: 2, lineStyle: LS.Solid  });
  l2tSeries = chart.addLineSeries({ ...seriesOpts, color: "#f97316", lineWidth: 2, lineStyle: LS.Dashed });
}

// ══════════════════════════════════════════════════════════════════════════════
// Scroll-Handler (Lazy-Load + Struktur-Refresh)
// ══════════════════════════════════════════════════════════════════════════════

function handleScroll() {
  if (isMutating) return;
  if (!chart)     return;

  const lr = chart.timeScale().getVisibleLogicalRange();
  if (!lr) return;

  // Lazy-Load: wenn nahe am linken Rand und noch nicht exhausted
  const key = `${currentSymbol}_${currentTF}`;
  const now = Date.now();
  if (
    lr.from < 80 &&
    !historyExhausted.has(key) &&
    !isLoadingHistory &&
    now - lastHistoryLoad > 1500
  ) {
    lastHistoryLoad = now;
    loadHistory(currentSymbol, currentTF, 1000, true);
  }

  // Struktur-Refresh mit Debounce
  if (structureDebounce) clearTimeout(structureDebounce);
  structureDebounce = setTimeout(() => {
    if (structureActive && !isLoadingHistory && !isLoadingStructure && !isMutating) {
      loadStructure();
    }
  }, 400);
}

// ══════════════════════════════════════════════════════════════════════════════
// Historische Kerzen laden
// ══════════════════════════════════════════════════════════════════════════════

async function loadHistory(symbol, timeframe, count = 1000, isPrepend = false) {
  if (isLoadingHistory) return;
  isLoadingHistory = true;
  if (!isPrepend) setLoading(true);

  try {
    let url = `${API_BASE}/api/history?symbol=${symbol}&timeframe=${timeframe}&count=${count}&_t=${Date.now()}`;
    if (isPrepend) url += `&offset=${allCandles.length}`;

    const resp = await fetch(url);
    if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail ?? `HTTP ${resp.status}`);

    const json    = await resp.json();
    const newBars = json.candles;
    if (!newBars || newBars.length === 0) {
      if (!isPrepend) showError(`Keine Daten für ${symbol} ${timeframe}`);
      return;
    }

    let savedRange = null;
    if (isPrepend && chart) savedRange = chart.timeScale().getVisibleLogicalRange();

    if (isPrepend) {
      const oldLen = allCandles.length;
      const mergeMap = new Map();
      [...newBars, ...allCandles].forEach(b => { if (b?.time != null) mergeMap.set(b.time, b); });
      allCandles = Array.from(mergeMap.values()).sort((a, b) => a.time - b.time);
      const added = allCandles.length - oldLen;
      if (added === 0) {
        historyExhausted.add(`${symbol}_${timeframe}`);
      } else if (savedRange && chart) {
        chart.timeScale().setVisibleLogicalRange({
          from: savedRange.from + added,
          to:   savedRange.to  + added,
        });
      }
    } else {
      allCandles = newBars;
    }

    candleSeries.setData(allCandles);

    const volData = allCandles
      .filter(b => b?.time != null)
      .map(b => ({
        time:  b.time,
        value: b.volume || 0,
        color: b.close >= b.open ? "#26a69a44" : "#ef535044",
      }));
    volumeSeries.setData(volData);

    if (!isPrepend && allCandles.length > 0) {
      const last = allCandles[allCandles.length - 1];
      lastPrice  = last.close;
      updatePriceDisplay(lastPrice, null);
      liveCandle = { ...last };
      await refreshIndicators();
      await loadStructure();
    }

  } catch (err) {
    console.error("[loadHistory]", err);
    showError(`Fehler beim Laden: ${err.message}`);
  } finally {
    setTimeout(() => { isLoadingHistory = false; setLoading(false); }, 250);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// WebSocket – Live-Ticks
// ══════════════════════════════════════════════════════════════════════════════

function connectWebSocket(symbol) {
  if (wsConn) { wsConn.onclose = null; wsConn.close(); wsConn = null; }
  if (wsRetryTimer) { clearTimeout(wsRetryTimer); wsRetryTimer = null; }

  setConnectionState("connecting");
  const ws = new WebSocket(`${WS_BASE}/ws/live?symbol=${symbol}`);
  wsConn = ws;

  ws.onopen    = () => setConnectionState("connected");
  ws.onerror   = () => setConnectionState("disconnected");
  ws.onmessage = (e) => { try { handleTick(JSON.parse(e.data)); } catch(_) {} };
  ws.onclose   = () => {
    setConnectionState("disconnected");
    wsRetryTimer = setTimeout(() => connectWebSocket(currentSymbol), 3000);
  };
}

function handleTick(msg) {
  if (msg.type !== "tick") return;

  const price  = msg.mid ?? msg.bid;
  const tickTs = isoToUnix(msg.time);
  const bt     = barTime(tickTs, tfToMin(currentTF));

  updatePriceDisplay(price, lastPrice);
  lastPrice = price;

  if (!liveCandle || liveCandle.time !== bt) {
    liveCandle = { time: bt, open: price, high: price, low: price, close: price };
  } else {
    liveCandle.high  = Math.max(liveCandle.high,  price);
    liveCandle.low   = Math.min(liveCandle.low,   price);
    liveCandle.close = price;
  }

  candleSeries.update(liveCandle);
}

// ══════════════════════════════════════════════════════════════════════════════
// Symbol- und Timeframe-Wechsel
// ══════════════════════════════════════════════════════════════════════════════

async function switchSymbol(symbol) {
  if (symbol === currentSymbol) return;
  currentSymbol = symbol;
  historyExhausted.clear();
  allCandles = [];
  clearAllIndicators();
  clearStructure();
  await loadHistory(symbol, currentTF);
  connectWebSocket(symbol);
}

async function switchTF(tf) {
  if (tf === currentTF) return;

  let centerTime = null;
  try {
    const lr = chart.timeScale().getVisibleLogicalRange();
    if (lr && allCandles.length > 0) {
      const ci = Math.max(0, Math.min(Math.round((lr.from + lr.to) / 2), allCandles.length - 1));
      centerTime = allCandles[ci].time;
    }
  } catch(_) {}

  currentTF        = tf;
  window.currentTF = tf;

  document.querySelectorAll(".tf-btn").forEach(btn =>
    btn.classList.toggle("active", btn.dataset.tf === tf)
  );

  historyExhausted.clear();
  allCandles = [];
  clearAllIndicators();
  resetIndicatorButtons();
  clearStructure();

  await loadHistory(currentSymbol, tf);

  if (centerTime && chart && allCandles.length > 0) {
    setTimeout(() => {
      try {
        let ci = 0, minDiff = Infinity;
        allCandles.forEach((c, i) => { const d = Math.abs(c.time - centerTime); if (d < minDiff) { minDiff = d; ci = i; } });
        const barsVisible = document.getElementById("chart").clientWidth / (chart.options().timeScale.barSpacing || 6);
        chart.timeScale().setVisibleLogicalRange({ from: ci - barsVisible / 2, to: ci + barsVisible / 2 });
      } catch(_) {}
    }, 100);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Symbol-Selector füllen
// ══════════════════════════════════════════════════════════════════════════════

async function loadSymbols() {
  try {
    const resp = await fetch(`${API_BASE}/api/symbols`);
    if (!resp.ok) return;
    const json   = await resp.json();
    const select = document.getElementById("symbol-select");
    select.innerHTML = "";
    for (const sym of json.symbols) {
      const opt       = document.createElement("option");
      opt.value       = sym;
      opt.textContent = sym;
      const match = sym === currentSymbol || (sym.startsWith(currentSymbol + ".") && sym.length <= currentSymbol.length + 3);
      if (match) { opt.selected = true; currentSymbol = sym; }
      select.appendChild(opt);
    }
    if (select.selectedIndex === -1 && select.options.length > 0) {
      select.options[0].selected = true;
      currentSymbol = select.options[0].value;
    }
  } catch(e) { console.warn("Symbole konnten nicht geladen werden:", e); }
}

// ══════════════════════════════════════════════════════════════════════════════
// Indikatoren (SMA / EMA)
// ══════════════════════════════════════════════════════════════════════════════

const INDICATOR_COLORS = {
  "SMA 50":  "#f59e0b",
  "SMA 200": "#a78bfa",
  "EMA 20":  "#34d399",
};

async function toggleIndicator(label, indName, period) {
  const btn = document.querySelector(`[data-ind="${label}"]`);
  if (activeIndicators.has(label)) {
    activeIndicators.delete(label);
    removeIndicatorLine(label);
    if (btn) btn.classList.remove("active");
    return;
  }
  activeIndicators.add(label);
  if (btn) btn.classList.add("active");
  try {
    const resp = await fetch(`${API_BASE}/api/indicator?name=${indName}&period=${period}&symbol=${currentSymbol}&timeframe=${currentTF}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    drawIndicatorLine(label, (await resp.json()).data, INDICATOR_COLORS[label] ?? "#888");
  } catch(e) {
    showError(`${label} konnte nicht geladen werden: ${e.message}`);
    activeIndicators.delete(label);
    if (btn) btn.classList.remove("active");
  }
}

async function refreshIndicators() {
  if (activeIndicators.size === 0) return;
  const PARAMS = {
    "SMA 50":  { name: "SMA", period: 50 },
    "SMA 200": { name: "SMA", period: 200 },
    "EMA 20":  { name: "EMA", period: 20 },
  };
  for (const label of activeIndicators) {
    const p = PARAMS[label];
    if (!p) continue;
    try {
      const resp = await fetch(`${API_BASE}/api/indicator?name=${p.name}&period=${p.period}&symbol=${currentSymbol}&timeframe=${currentTF}`);
      if (resp.ok) drawIndicatorLine(label, (await resp.json()).data, INDICATOR_COLORS[label] ?? "#888");
    } catch(e) { console.warn(`Indikator ${label} refresh fehlgeschlagen:`, e); }
  }
}

function drawIndicatorLine(name, data, color) {
  if (indicatorSeries.has(name)) chart.removeSeries(indicatorSeries.get(name));
  const s = chart.addLineSeries({ color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false });
  s.setData(data);
  indicatorSeries.set(name, s);
  indicatorMeta.set(name, { color });
  updateLegend();
}

function removeIndicatorLine(name) {
  if (!indicatorSeries.has(name)) return;
  chart.removeSeries(indicatorSeries.get(name));
  indicatorSeries.delete(name);
  indicatorMeta.delete(name);
  updateLegend();
}

function clearAllIndicators() {
  for (const [, s] of indicatorSeries) chart.removeSeries(s);
  indicatorSeries.clear();
  indicatorMeta.clear();
  updateLegend();
}

function resetIndicatorButtons() {
  document.querySelectorAll(".ind-btn").forEach(btn => btn.classList.remove("active"));
  activeIndicators.clear();
}

function updateLegend() {
  const legend = document.getElementById("indicator-legend");
  if (!legend) return;
  legend.innerHTML = "";
  for (const [name, meta] of indicatorMeta) {
    const item = document.createElement("div");
    item.className = "legend-item";
    item.innerHTML = `<span class="legend-dot" style="background:${meta.color}"></span><span>${name}</span>`;
    legend.appendChild(item);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// Bottom-Up Struktur – Level 1 & 2
// ══════════════════════════════════════════════════════════════════════════════

/**
 * Schreibt Daten in eine Struktur-Serie.
 *
 * @param {LineSeries} series   – die LWC-Serie
 * @param {Array}      pivots   – [{time, price}, ...] vom Backend
 * @param {boolean}    visible  – ob Nutzer diese Serie sehen möchte
 *
 * Ablauf:
 *   1. pivotToLwcData() – deduplizieren + sortieren + in {time, value} umwandeln
 *   2. series.setData()         – rendert die Linie
 *   3. series.applyOptions()    – visible flag setzen
 */
function writeStructureSeries(series, pivots, visible) {
  const data = pivotToLwcData(pivots);
  if (data.length >= 1) {
    series.setData(data);
    series.applyOptions({ visible });
  } else {
    series.setData([]);
    series.applyOptions({ visible: false });
  }
}

/**
 * Zeichnet die Bottom-Up Struktur (Level 1 + 2) auf dem Chart.
 *
 * isMutating Flag schützt gegen Feedback-Loop:
 *   setData → subscribeVisibleTimeRangeChange → handleScroll → loadStructure
 *   → drawStructure → setData → ... (Endlosschleife)
 */
function drawStructure(data) {
  if (!data) return;
  lastStructureData = data;

  isMutating = true;
  const savedRange = chart?.timeScale().getVisibleLogicalRange() ?? null;

  try {
    writeStructureSeries(l1Series,  data.level_1,      structureActive && levelVisible.l1);
    writeStructureSeries(l1tSeries, data.level_1_temp, structureActive && levelVisible.l1t);
    writeStructureSeries(l2Series,  data.level_2,      structureActive && levelVisible.l2);
    writeStructureSeries(l2tSeries, data.level_2_temp, structureActive && levelVisible.l2t);
  } finally {
    setTimeout(() => {
      if (savedRange && chart) chart.timeScale().setVisibleLogicalRange(savedRange);
      isMutating = false;
    }, 50);
  }

  updateTrendBadge(data);
}

/** Versteckt alle Struktur-Serien (ohne Daten zu löschen). */
function clearStructure() {
  isMutating = true;
  const savedRange = chart?.timeScale().getVisibleLogicalRange() ?? null;

  try {
    [l1Series, l1tSeries, l2Series, l2tSeries].forEach(s => {
      if (s) { s.setData([]); s.applyOptions({ visible: false }); }
    });
  } finally {
    setTimeout(() => {
      if (savedRange && chart) chart.timeScale().setVisibleLogicalRange(savedRange);
      isMutating = false;
    }, 50);
  }

  const badge = document.getElementById("trend-badge");
  if (badge) badge.className = "trend-badge hidden";
}

/**
 * Lädt Struktur-Daten vom Backend und zeichnet sie.
 * Verwendet /api/structure_bu (Bottom-Up Engine).
 */
async function loadStructure() {
  if (!structureActive || isLoadingStructure || isLoadingHistory || isMutating) return;

  let url = `${API_BASE}/api/structure_bu?symbol=${currentSymbol}&timeframe=${currentTF}&count=800&pivot_length=${currentPivotLength}`;

  if (chart && allCandles.length > 0) {
    const lr = chart.timeScale().getVisibleLogicalRange();
    if (lr?.from != null && lr?.to != null) {
      const idxFrom = Math.max(0, Math.min(allCandles.length - 1, Math.floor(lr.from)));
      const idxTo   = Math.max(0, Math.min(allCandles.length - 1, Math.ceil(lr.to)));
      url += `&viewport_start=${allCandles[idxFrom].time}&viewport_end=${allCandles[idxTo].time}`;
    }
  }

  try {
    isLoadingStructure = true;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    drawStructure(await resp.json());
  } catch(e) {
    console.error("[loadStructure]", e);
  } finally {
    isLoadingStructure = false;
  }
}

/** Ein-/Ausschalten des Struktur-Overlays */
async function toggleStructure() {
  const btn    = document.getElementById("structure-btn");
  const slider = document.getElementById("pivot-control-box");
  structureActive = !structureActive;

  if (structureActive) {
    if (btn)    btn.classList.add("active");
    if (slider) slider.classList.remove("hidden");
    await loadStructure();
  } else {
    if (btn)    btn.classList.remove("active");
    if (slider) slider.classList.add("hidden");
    clearStructure();
  }
}

/**
 * Schaltet eine einzelne Level-Serie an/aus.
 * Wird von den Level-Toggle-Buttons in der Toolbar aufgerufen.
 *
 * @param {string} key – "l1" | "l1t" | "l2" | "l2t"
 */
function toggleLevel(key) {
  levelVisible[key] = !levelVisible[key];

  const btn    = document.getElementById(`toggle-${key}`);
  const series = seriesMap()[key];

  if (btn) btn.classList.toggle("active", levelVisible[key]);

  // Sichtbarkeit direkt auf der Serie setzen – kein neues loadStructure nötig
  if (series) series.applyOptions({ visible: structureActive && levelVisible[key] });
}

// Trend-Badge
function updateTrendBadge(data) {
  const badge = document.getElementById("trend-badge");
  if (!badge) return;
  if (!data) { badge.className = "trend-badge hidden"; return; }

  const l1 = data.trend_l1 || "Neutral";
  const l2 = data.trend_l2 || "Neutral";
  badge.className = "trend-badge visible";
  badge.innerHTML = `
    <div class="badge-row">
      <span class="badge-label" style="color:#00e5ff">L1</span>
      <span class="badge-value ${l1.toLowerCase()}">${l1}</span>
    </div>
    <div class="badge-row">
      <span class="badge-label" style="color:#f97316">L2</span>
      <span class="badge-value ${l2.toLowerCase()}">${l2}</span>
    </div>
  `;
}

// Volumen-Toggle
function toggleVolume() {
  if (!volumeSeries) return;
  const btn     = document.getElementById("volume-toggle-btn");
  const visible = !volumeSeries.options().visible;
  volumeSeries.applyOptions({ visible });
  if (btn) btn.classList.toggle("active", visible);
}

// ══════════════════════════════════════════════════════════════════════════════
// App starten
// ══════════════════════════════════════════════════════════════════════════════

async function initApp() {
  document.getElementById("structure-btn")?.addEventListener("click", toggleStructure);
  document.getElementById("symbol-select")?.addEventListener("change", e => switchSymbol(e.target.value));
  document.querySelectorAll(".tf-btn").forEach(btn =>
    btn.addEventListener("click", () => switchTF(btn.dataset.tf))
  );

  // Level-Toggle-Buttons
  document.querySelectorAll(".lvl-btn").forEach(btn => {
    btn.addEventListener("click", () => toggleLevel(btn.dataset.series));
  });

  // Pivot-Stärke Slider
  const slider  = document.getElementById("pivot-slider");
  const display = document.getElementById("pivot-val-display");
  if (slider && display) {
    slider.addEventListener("input",  e => { currentPivotLength = +e.target.value; display.textContent = currentPivotLength; });
    slider.addEventListener("change", () => { if (structureActive) loadStructure(); });
  }

  initChart();
  await loadHistory(currentSymbol, currentTF);
  connectWebSocket(currentSymbol);
  await loadSymbols();
}

document.addEventListener("DOMContentLoaded", initApp);
