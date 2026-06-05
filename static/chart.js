/**
 * chart.js – TradingView Lightweight Charts Integration
 *
 * Verantwortlich für:
 *  - Chart initialisieren (Candlestick-Series)
 *  - Historische Daten von /api/history laden
 *  - Live-Updates per WebSocket (/ws/live) empfangen und aggregieren
 *  - Symbol- und Timeframe-Wechsel
 *  - Indikator-Linien über drawIndicatorLine() zeichnen
 *  - Struktur-Overlay (Top-Down H4): Micro-Pivots (lila), H4 Master (gelb), H1/M15/M5/M1 Inner
 *  - Struktur-Overlay (Bottom-Up):   Level 0 (lila), Level 1 (cyan), Level 2 (orange), Level 3 (grün)
 */

"use strict";

// ── Konfiguration ──────────────────────────────────────────────────────────────
const API_BASE  = "";
const WS_PROTO  = location.protocol === "https:" ? "wss:" : "ws:";
const WS_BASE   = `${WS_PROTO}//${location.host}`;

// ── State ──────────────────────────────────────────────────────────────────────
let chart          = null;
let candleSeries   = null;
let volumeSeries   = null;
let wsConnection   = null;
let currentSymbol  = "XAUUSD";
let currentTF      = "5m";
window.currentTF   = currentTF;
let liveCandle     = null;
let wsReconnectTimer = null;
let currentPivotLength = 2;
let lastPrice          = 0;

let isLoadingHistory  = false;
let lastLoadTimestamp = 0;
let historyEndReached = new Set();
let allCandles       = [];
let structureDebounceTimer = null;

/**
 * ⚠️ KRITISCH – Nicht entfernen!
 * Verhindert die Feedback-Loop: drawStructure → addLineSeries → Event → handleScroll → ...
 */
let isMutatingChart = false;

/** Map: name → ISeriesApi<"Line"> */
const indicatorSeries = new Map();
const indicatorMeta   = new Map();

const TF_MINUTES = { "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440 };

// ── Struktur-Modus ─────────────────────────────────────────────────────────────
// "topdown" = bestehende H4-Engine  |  "bottomup" = neue fraktale Bottom-Up Engine
let structureMode   = "topdown";  // Startwert
let structureActive = false;

// ── Top-Down Serien ────────────────────────────────────────────────────────────
let structureMicroSeries = null;
let structureH4Series    = null;

let seriesPoolH4Temp   = [];
let seriesPoolH4Proj   = [];
let seriesPoolH1Inner  = [];
let seriesPoolH1Proj   = [];
let seriesPoolM15Inner = [];
let seriesPoolM15Proj  = [];
let seriesPoolM5Inner  = [];
let seriesPoolM5Proj   = [];
let seriesPoolM1Inner  = [];
let seriesPoolM1Proj   = [];

// ── Bottom-Up Serien ───────────────────────────────────────────────────────────
let buLevel0Series  = null;   // Lila  – M1 Micro
let seriesPoolBuL1  = [];     // Cyan  – Level 1 confirmed
let seriesPoolBuL1T = [];     // Cyan dashed – Level 1 temp
let seriesPoolBuL2  = [];     // Orange – Level 2 confirmed
let seriesPoolBuL2T = [];     // Orange dashed – Level 2 temp
let seriesPoolBuL3  = [];     // Grün – Level 3 confirmed
let seriesPoolBuL3T = [];     // Grün dashed – Level 3 temp

/** Sichtbarkeit der Layer */
const layerVisibility = { micro: true, h4_master: true, h1_inner: true, m15_inner: true, m5_inner: true, m1_inner: true };

let lastStructureData = null;

// ── Series-Pool Helfer ─────────────────────────────────────────────────────────

function rebuildSeriesPool(pool, paths, opts) {
  const needed = (paths || []).length;
  pool.forEach(s => { s.setData([]); s.applyOptions({ visible: false }); });
  if (needed === 0) return;

  const LineStyle = LightweightCharts.LineStyle;
  const baseOpts  = { ...opts, visible: false };

  for (let i = 0; i < needed; i++) {
    const path = paths[i];
    if (!path || !Array.isArray(path) || path.length < 2) continue;

    const map = new Map();
    path.forEach(p => { if (p && p.time !== undefined) map.set(p.time, { time: p.time, value: p.price }); });
    const data = Array.from(map.values()).sort((a, b) => a.time - b.time);
    if (data.length < 2) continue;

    let s;
    if (i < pool.length) { s = pool[i]; s.applyOptions(baseOpts); }
    else                  { s = chart.addLineSeries(baseOpts); pool.push(s); }
    s.setData(data);
  }
}

/**
 * Wie rebuildSeriesPool, aber erwartet flache Arrays (nicht Arrays von Arrays).
 * Wird für Bottom-Up Level genutzt, die bereits als einzelner Pfad vorliegen.
 */
function rebuildSinglePathPool(pool, points, opts) {
  // Wrap als Einzel-Pfad und delegate
  rebuildSeriesPool(pool, points && points.length >= 2 ? [points] : [], opts);
}

function setPoolVisibility(pool, visible) {
  pool.forEach(s => { try { s.applyOptions({ visible }); } catch(e) {} });
}

// ── Hilfsfunktionen ────────────────────────────────────────────────────────────

function tfToMinutes(tf)           { return TF_MINUTES[tf] ?? 60; }
function getBarTime(unixSec, tfMin){ const s = tfMin * 60; return Math.floor(unixSec / s) * s; }
function isoToUnix(isoStr)         { return Math.floor(new Date(isoStr).getTime() / 1000); }

function setConnectionState(state) {
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
  else    overlay.classList.add("hidden");
}

function updatePriceDisplay(price, prevPrice) {
  const el = document.getElementById("price-display");
  el.textContent = price != null ? price.toFixed(price > 100 ? 2 : 5) : "—";
  if (prevPrice == null || price === prevPrice) el.className = "";
  else el.className = price > prevPrice ? "up" : "down";
}

// ── Chart initialisieren ───────────────────────────────────────────────────────

function initChart() {
  const container = document.getElementById("chart");
  const LineStyle  = LightweightCharts.LineStyle;

  chart = LightweightCharts.createChart(container, {
    layout:          { background: { color: "#131722" }, textColor: "#d1d4dc" },
    grid:            { vertLines: { color: "#1e222d" }, horzLines: { color: "#1e222d" } },
    crosshair:       { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "#363a45" },
    timeScale:       { borderColor: "#363a45", timeVisible: true, secondsVisible: false, rightOffset: 8, barSpacing: 8, minBarSpacing: 3 },
    watermark:       { visible: false },
  });

  candleSeries = chart.addCandlestickSeries({
    upColor: "#26a69a", downColor: "#ef5350",
    borderUpColor: "#26a69a", borderDownColor: "#ef5350",
    wickUpColor: "#26a69a", wickDownColor: "#ef5350",
  });

  volumeSeries = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "volume", color: "#26a69a44" });
  chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

  const resizeObs = new ResizeObserver(() => chart.applyOptions({ width: container.clientWidth, height: container.clientHeight }));
  resizeObs.observe(container);
  chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });

  chart.timeScale().subscribeVisibleTimeRangeChange(() => handleScroll());

  // ── Top-Down Serien (einmalig) ───────────────────────────────────────────
  structureMicroSeries = chart.addLineSeries({ color: "#d44bec", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, lineStyle: LineStyle.Solid, visible: false });
  structureH4Series    = chart.addLineSeries({ color: "#facc15", lineWidth: 3, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, lineStyle: LineStyle.Solid, visible: false });

  // ── Bottom-Up Level-0 Serie (einmalig) ───────────────────────────────────
  buLevel0Series = chart.addLineSeries({ color: "#d44bec", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, lineStyle: LineStyle.Solid, visible: false });
}

// ── Viewport & Scrolling ───────────────────────────────────────────────────────

function handleScroll() {
  if (isMutatingChart) return;
  if (!chart || isLoadingHistory || isLoadingStructure) return;

  const logicalRange = chart.timeScale().getVisibleLogicalRange();
  if (!logicalRange) return;

  const historyKey = `${currentSymbol}_${currentTF}`;
  const now = Date.now();
  if (logicalRange.from < 100 && !historyEndReached.has(historyKey) && (now - lastLoadTimestamp > 1500)) {
    lastLoadTimestamp = now;
    loadHistory(currentSymbol, currentTF, 1000, true);
  }

  if (structureDebounceTimer) clearTimeout(structureDebounceTimer);
  structureDebounceTimer = setTimeout(() => {
    if (structureActive && !isLoadingHistory && !isLoadingStructure && !isMutatingChart) {
      loadStructure(currentSymbol, currentTF);
    }
  }, 400);
}

// ── Historische Daten laden ────────────────────────────────────────────────────

async function loadHistory(symbol, timeframe, count = 1000, isPrepend = false) {
  if (isLoadingHistory) return;
  isLoadingHistory = true;
  if (!isPrepend) setLoading(true);

  try {
    let url = `${API_BASE}/api/history?symbol=${symbol}&timeframe=${timeframe}&count=${count}&_t=${Date.now()}`;
    if (isPrepend) url += `&offset=${allCandles.length}`;

    const resp = await fetch(url);
    if (!resp.ok) { const err = await resp.json().catch(() => ({})); throw new Error(err.detail ?? `HTTP ${resp.status}`); }

    const json    = await resp.json();
    const newBars = json.candles;
    if (!newBars || newBars.length === 0) { if (!isPrepend) showError(`Keine Daten für ${symbol} ${timeframe}`); return; }

    let oldLogicalRange = null;
    if (isPrepend && chart) oldLogicalRange = chart.timeScale().getVisibleLogicalRange();

    if (isPrepend) {
      const oldLen  = allCandles.length;
      const uniqueMap = new Map();
      [...newBars, ...allCandles].filter(b => b && b.time != null).forEach(b => uniqueMap.set(b.time, b));
      allCandles = Array.from(uniqueMap.values()).sort((a, b) => a.time - b.time);
      const added = allCandles.length - oldLen;
      if (added === 0) historyEndReached.add(`${symbol}_${timeframe}`);
      if (added > 0 && oldLogicalRange && chart) {
        chart.timeScale().setVisibleLogicalRange({ from: oldLogicalRange.from + added, to: oldLogicalRange.to + added });
      }
    } else {
      allCandles = newBars;
    }

    candleSeries.setData(allCandles);
    const volData = allCandles.filter(b => b && b.time != null).map(b => ({ time: b.time, value: b.volume || 0, color: b.close >= b.open ? "#26a69a44" : "#ef535044" }));
    volumeSeries.setData(volData);

    if (!isPrepend) {
      if (allCandles.length > 0) { const last = allCandles[allCandles.length - 1]; lastPrice = last.close; updatePriceDisplay(lastPrice, null); liveCandle = { ...last }; }
      await refreshActiveIndicators(symbol, timeframe, count);
      await loadStructure(symbol, timeframe);
    }
  } catch (err) {
    console.error("[Chart] loadHistory error:", err);
  } finally {
    setTimeout(() => { isLoadingHistory = false; setLoading(false); }, 250);
  }
}

// ── WebSocket – Live-Ticks ─────────────────────────────────────────────────────

function connectWebSocket(symbol) {
  if (wsConnection)    { wsConnection.onclose = null; wsConnection.close(); wsConnection = null; }
  if (wsReconnectTimer){ clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }

  setConnectionState("connecting");
  const ws = new WebSocket(`${WS_BASE}/ws/live?symbol=${symbol}`);
  wsConnection = ws;
  ws.onopen    = () => setConnectionState("connected");
  ws.onerror   = ()  => setConnectionState("disconnected");
  ws.onmessage = (e) => { try { handleWsMessage(JSON.parse(e.data)); } catch(_) {} };
  ws.onclose   = ()  => { setConnectionState("disconnected"); wsReconnectTimer = setTimeout(() => connectWebSocket(currentSymbol), 3000); };
}

function handleWsMessage(msg) {
  if (msg.type !== "tick") return;
  const price   = msg.mid ?? msg.bid;
  const tickTs  = isoToUnix(msg.time);
  const barTime = getBarTime(tickTs, tfToMinutes(currentTF));
  updatePriceDisplay(price, lastPrice);
  lastPrice = price;
  if (!liveCandle || liveCandle.time !== barTime) liveCandle = { time: barTime, open: price, high: price, low: price, close: price };
  else { liveCandle.high = Math.max(liveCandle.high, price); liveCandle.low = Math.min(liveCandle.low, price); liveCandle.close = price; }
  candleSeries.update(liveCandle);
}

async function switchSymbol(symbol) {
  if (symbol === currentSymbol) return;
  historyEndReached.clear();
  currentSymbol = symbol;
  clearAllIndicators();
  clearStructure();
  allCandles = [];
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

  currentTF = tf;
  window.currentTF = tf;
  document.querySelectorAll(".tf-btn").forEach(btn => btn.classList.toggle("active", btn.dataset.tf === tf));
  historyEndReached.clear();
  clearAllIndicators();
  resetActiveIndicatorButtons();
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

// ── Symbol-Selector ────────────────────────────────────────────────────────────

async function loadSymbols() {
  try {
    const resp = await fetch(`${API_BASE}/api/symbols`);
    if (!resp.ok) return;
    const json   = await resp.json();
    const select = document.getElementById("symbol-select");
    select.innerHTML = "";
    for (const sym of json.symbols) {
      const opt   = document.createElement("option");
      opt.value   = sym;
      opt.textContent = sym;
      const isMatch = (sym === currentSymbol) || (sym.startsWith(currentSymbol + ".") && sym.length <= currentSymbol.length + 3);
      if (isMatch) { opt.selected = true; currentSymbol = sym; }
      select.appendChild(opt);
    }
    if (select.selectedIndex === -1 && select.options.length > 0) { select.options[0].selected = true; currentSymbol = select.options[0].value; }
  } catch(e) { console.warn("Symbole konnten nicht geladen werden:", e); }
}

// ── Indikator-Linien ───────────────────────────────────────────────────────────

function drawIndicatorLine(name, data, color = "#f59e0b", lineWidth = 1.5) {
  if (indicatorSeries.has(name)) chart.removeSeries(indicatorSeries.get(name));
  const series = chart.addLineSeries({ color, lineWidth, priceLineVisible: false, lastValueVisible: true, crosshairMarkerVisible: false });
  series.setData(data);
  indicatorSeries.set(name, series);
  indicatorMeta.set(name, { color, lineWidth });
  updateLegend();
}

function removeIndicatorLine(name) {
  if (indicatorSeries.has(name)) { chart.removeSeries(indicatorSeries.get(name)); indicatorSeries.delete(name); indicatorMeta.delete(name); updateLegend(); }
}

function clearAllIndicators() {
  for (const [, s] of indicatorSeries) chart.removeSeries(s);
  indicatorSeries.clear(); indicatorMeta.clear(); updateLegend();
}

const INDICATOR_COLORS = { "SMA 50": "#f59e0b", "SMA 200": "#a78bfa", "EMA 20": "#34d399", "RSI 14": "#60a5fa" };
const activeIndicators = new Set();

async function toggleIndicator(label, indName, period) {
  const btn = document.querySelector(`[data-ind="${label}"]`);
  if (activeIndicators.has(label)) { activeIndicators.delete(label); removeIndicatorLine(label); if (btn) btn.classList.remove("active"); return; }
  activeIndicators.add(label);
  if (btn) btn.classList.add("active");
  try {
    const resp = await fetch(`${API_BASE}/api/indicator?name=${indName}&period=${period}&symbol=${currentSymbol}&timeframe=${currentTF}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const json  = await resp.json();
    drawIndicatorLine(label, json.data, INDICATOR_COLORS[label] ?? "#888");
  } catch(e) { console.error(`Indikator ${label} Fehler:`, e); showError(`${label} konnte nicht geladen werden: ${e.message}`); activeIndicators.delete(label); if (btn) btn.classList.remove("active"); }
}

async function refreshActiveIndicators(symbol, timeframe, count) {
  if (activeIndicators.size === 0) return;
  const PARAMS = { "SMA 50": { name: "SMA", period: 50 }, "SMA 200": { name: "SMA", period: 200 }, "EMA 20": { name: "EMA", period: 20 }, "RSI 14": { name: "RSI", period: 14 } };
  for (const label of activeIndicators) {
    const p = PARAMS[label]; if (!p) continue;
    try {
      const resp = await fetch(`${API_BASE}/api/indicator?name=${p.name}&period=${p.period}&symbol=${symbol}&timeframe=${timeframe}&count=${count}`);
      if (!resp.ok) continue;
      const json = await resp.json();
      drawIndicatorLine(label, json.data, INDICATOR_COLORS[label] ?? "#888");
    } catch(e) { console.warn(`Indikator ${label} refresh fehlgeschlagen:`, e); }
  }
}

function resetActiveIndicatorButtons() {
  document.querySelectorAll(".ind-btn").forEach(btn => btn.classList.remove("active"));
  activeIndicators.clear();
}

function updateLegend() {
  const legend = document.getElementById("indicator-legend");
  legend.innerHTML = "";
  for (const [name, meta] of indicatorMeta) {
    const item = document.createElement("div");
    item.className = "legend-item";
    item.innerHTML = `<span class="legend-dot" style="background:${meta.color}"></span><span>${name}</span>`;
    legend.appendChild(item);
  }
}

// =============================================================================
// TOP-DOWN STRUKTUR (bestehende H4-Engine)
// =============================================================================

function pivotToPoint(p) { return { time: p.time, value: p.price }; }
function isH1Eligible(tf)  { return tfToMinutes(tf) <= 60; }
function isM15Eligible(tf) { return tfToMinutes(tf) <= 15; }
function isM5Eligible(tf)  { return tfToMinutes(tf) <= 5; }
function isM1Eligible(tf)  { return tfToMinutes(tf) <= 1; }

function clearTopDownStructure() {
  const LineStyle = LightweightCharts.LineStyle;
  if (structureMicroSeries) structureMicroSeries.setData([]);
  if (structureH4Series)    structureH4Series.setData([]);
  [
    [seriesPoolH4Temp, {}], [seriesPoolH4Proj, {}],
    [seriesPoolH1Inner, {}], [seriesPoolH1Proj, {}],
    [seriesPoolM15Inner, {}], [seriesPoolM15Proj, {}],
    [seriesPoolM5Inner, {}], [seriesPoolM5Proj, {}],
    [seriesPoolM1Inner, {}], [seriesPoolM1Proj, {}],
  ].forEach(([pool, opts]) => rebuildSeriesPool(pool, [], opts));
}

function drawTopDownStructure(data) {
  if (!data) return;
  const LineStyle = LightweightCharts.LineStyle;

  // Lila Micro
  if (structureMicroSeries) {
    if (structureActive && data.micro_pivots && data.micro_pivots.length >= 2) {
      const microMap = new Map();
      data.micro_pivots.forEach(p => { if (p && p.time != null && p.price != null) microMap.set(p.time, { time: p.time, value: p.price }); });
      const microData = Array.from(microMap.values()).sort((a, b) => a.time - b.time);
      if (microData.length >= 2) { structureMicroSeries.setData(microData); structureMicroSeries.applyOptions({ visible: layerVisibility.micro }); }
      else { structureMicroSeries.setData([]); }
    } else { structureMicroSeries.setData([]); structureMicroSeries.applyOptions({ visible: false }); }
  }

  // H4 Master (gelb)
  if (structureH4Series) {
    if (structureActive && data.h4_master_pivots && data.h4_master_pivots.length >= 2) {
      const h4Data = data.h4_master_pivots.filter(p => p && p.time != null && p.price != null).map(p => ({ time: p.time, value: p.price }));
      if (h4Data.length >= 2) { structureH4Series.setData(h4Data); structureH4Series.applyOptions({ visible: layerVisibility.h4_master }); }
      else structureH4Series.setData([]);
    } else { structureH4Series.setData([]); structureH4Series.applyOptions({ visible: false }); }
  }

  const showH4  = structureActive && layerVisibility.h4_master;
  const showH1  = structureActive && isH1Eligible(currentTF)  && layerVisibility.h1_inner;
  const showM15 = structureActive && isM15Eligible(currentTF) && layerVisibility.m15_inner;
  const showM5  = structureActive && isM5Eligible(currentTF)  && layerVisibility.m5_inner;
  const showM1  = structureActive && isM1Eligible(currentTF)  && layerVisibility.m1_inner;

  rebuildSeriesPool(seriesPoolH4Temp,   data.h4_temp_pivots,        { color: '#facc15', lineWidth: 2, lineStyle: LineStyle.Dashed,  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }); setPoolVisibility(seriesPoolH4Temp, showH4);
  rebuildSeriesPool(seriesPoolH4Proj,   data.h4_projected_pivots,   { color: '#facc15', lineWidth: 2, lineStyle: LineStyle.Dotted,  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }); setPoolVisibility(seriesPoolH4Proj, showH4);
  rebuildSeriesPool(seriesPoolH1Inner,  data.h1_inner_structure,    { color: '#4ade80', lineWidth: 1, lineStyle: LineStyle.Solid,   priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }); setPoolVisibility(seriesPoolH1Inner, showH1);
  rebuildSeriesPool(seriesPoolH1Proj,   data.h1_projected_pivots,   { color: '#4ade80', lineWidth: 1, lineStyle: LineStyle.Dotted,  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }); setPoolVisibility(seriesPoolH1Proj, showH1);
  rebuildSeriesPool(seriesPoolM15Inner, data.m15_inner_structure,   { color: '#00e5ff', lineWidth: 1, lineStyle: LineStyle.Solid,   priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }); setPoolVisibility(seriesPoolM15Inner, showM15);
  rebuildSeriesPool(seriesPoolM15Proj,  data.m15_projected_pivots,  { color: '#00e5ff', lineWidth: 1, lineStyle: LineStyle.Dotted,  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }); setPoolVisibility(seriesPoolM15Proj, showM15);
  rebuildSeriesPool(seriesPoolM5Inner,  data.m5_inner_structure,    { color: '#f97316', lineWidth: 1, lineStyle: LineStyle.Solid,   priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }); setPoolVisibility(seriesPoolM5Inner, showM5);
  rebuildSeriesPool(seriesPoolM5Proj,   data.m5_projected_pivots,   { color: '#f97316', lineWidth: 1, lineStyle: LineStyle.Dotted,  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }); setPoolVisibility(seriesPoolM5Proj, showM5);
  rebuildSeriesPool(seriesPoolM1Inner,  data.m1_inner_structure,    { color: '#ec4899', lineWidth: 1, lineStyle: LineStyle.Solid,   priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }); setPoolVisibility(seriesPoolM1Inner, showM1);
  rebuildSeriesPool(seriesPoolM1Proj,   data.m1_projected_pivots,   { color: '#ec4899', lineWidth: 1, lineStyle: LineStyle.Dotted,  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }); setPoolVisibility(seriesPoolM1Proj, showM1);

  updateTrendBadge(data);
}

// =============================================================================
// BOTTOM-UP STRUKTUR
// =============================================================================

function clearBottomUpStructure() {
  if (buLevel0Series) buLevel0Series.setData([]);
  [seriesPoolBuL1, seriesPoolBuL1T, seriesPoolBuL2, seriesPoolBuL2T, seriesPoolBuL3, seriesPoolBuL3T]
    .forEach(pool => rebuildSeriesPool(pool, [], {}));
}

function drawBottomUpStructure(data) {
  if (!data) return;
  const LineStyle = LightweightCharts.LineStyle;

  // Level 0 – Lila Micro (single series, kein Pool nötig)
  if (buLevel0Series) {
    if (structureActive && data.level_0 && data.level_0.length >= 2) {
      const pts = data.level_0.map(p => ({ time: p.time, value: p.price })).sort((a, b) => a.time - b.time);
      buLevel0Series.setData(pts);
      buLevel0Series.applyOptions({ visible: true });
    } else {
      buLevel0Series.setData([]);
      buLevel0Series.applyOptions({ visible: false });
    }
  }

  // Level 1 – Cyan
  rebuildSinglePathPool(seriesPoolBuL1,  data.level_1,      { color: '#00e5ff', lineWidth: 1, lineStyle: LineStyle.Solid,  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  rebuildSinglePathPool(seriesPoolBuL1T, data.level_1_temp, { color: '#00e5ff', lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  setPoolVisibility(seriesPoolBuL1,  structureActive);
  setPoolVisibility(seriesPoolBuL1T, structureActive);

  // Level 2 – Orange
  rebuildSinglePathPool(seriesPoolBuL2,  data.level_2,      { color: '#f97316', lineWidth: 2, lineStyle: LineStyle.Solid,  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  rebuildSinglePathPool(seriesPoolBuL2T, data.level_2_temp, { color: '#f97316', lineWidth: 2, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  setPoolVisibility(seriesPoolBuL2,  structureActive);
  setPoolVisibility(seriesPoolBuL2T, structureActive);

  // Level 3 – Grün
  rebuildSinglePathPool(seriesPoolBuL3,  data.level_3,      { color: '#4ade80', lineWidth: 3, lineStyle: LineStyle.Solid,  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  rebuildSinglePathPool(seriesPoolBuL3T, data.level_3_temp, { color: '#4ade80', lineWidth: 3, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  setPoolVisibility(seriesPoolBuL3,  structureActive);
  setPoolVisibility(seriesPoolBuL3T, structureActive);

  // Trend-Badge für Bottom-Up Modus
  updateTrendBadgeBU(data);
}

function updateTrendBadgeBU(data) {
  const badge = document.getElementById("trend-badge");
  if (!badge) return;
  if (!data) { badge.className = "trend-badge hidden"; return; }
  badge.className = "trend-badge visible";
  const l1 = data.trend_l1 || "Neutral";
  const l2 = data.trend_l2 || "Neutral";
  const l3 = data.trend_l3 || "Neutral";
  badge.innerHTML = `
    <div class="badge-row"><span class="badge-label">L1 (Cyan):</span>   <span class="badge-value ${l1.toLowerCase()}">${l1}</span></div>
    <div class="badge-row"><span class="badge-label">L2 (Orange):</span> <span class="badge-value ${l2.toLowerCase()}">${l2}</span></div>
    <div class="badge-row"><span class="badge-label">L3 (Grün):</span>   <span class="badge-value ${l3.toLowerCase()}">${l3}</span></div>
  `;
}

// =============================================================================
// GEMEINSAME STRUKTUR-FUNKTIONEN
// =============================================================================

function drawStructure(data) {
  if (!data) return;
  lastStructureData = data;
  isMutatingChart = true;
  const _savedRange = chart ? chart.timeScale().getVisibleLogicalRange() : null;

  try {
    if (structureMode === "bottomup") {
      clearTopDownStructure();
      drawBottomUpStructure(data);
    } else {
      clearBottomUpStructure();
      drawTopDownStructure(data);
    }
  } finally {
    setTimeout(() => {
      if (_savedRange && chart) chart.timeScale().setVisibleLogicalRange(_savedRange);
      isMutatingChart = false;
    }, 50);
  }
}

function clearStructure() {
  isMutatingChart = true;
  const _savedRange = chart ? chart.timeScale().getVisibleLogicalRange() : null;
  try {
    clearTopDownStructure();
    clearBottomUpStructure();
    updateTrendBadge(null);
  } finally {
    setTimeout(() => {
      if (_savedRange && chart) chart.timeScale().setVisibleLogicalRange(_savedRange);
      isMutatingChart = false;
    }, 50);
  }
}

function toggleStructureLayer(btn) {
  const layer = btn.dataset.layer;
  layerVisibility[layer] = !layerVisibility[layer];
  btn.classList.toggle("active", layerVisibility[layer]);
  if (!structureActive || !lastStructureData) return;

  switch (layer) {
    case "micro":      if (structureMicroSeries) structureMicroSeries.applyOptions({ visible: layerVisibility.micro }); break;
    case "h4_master":  if (structureH4Series) structureH4Series.applyOptions({ visible: layerVisibility.h4_master }); setPoolVisibility(seriesPoolH4Temp, layerVisibility.h4_master); setPoolVisibility(seriesPoolH4Proj, layerVisibility.h4_master); break;
    case "h1_inner":   { const v = layerVisibility.h1_inner  && isH1Eligible(currentTF);  setPoolVisibility(seriesPoolH1Inner, v);  setPoolVisibility(seriesPoolH1Proj, v);  break; }
    case "m15_inner":  { const v = layerVisibility.m15_inner && isM15Eligible(currentTF); setPoolVisibility(seriesPoolM15Inner, v); setPoolVisibility(seriesPoolM15Proj, v); break; }
    case "m5_inner":   { const v = layerVisibility.m5_inner  && isM5Eligible(currentTF);  setPoolVisibility(seriesPoolM5Inner, v);  setPoolVisibility(seriesPoolM5Proj, v);  break; }
    case "m1_inner":   { const v = layerVisibility.m1_inner  && isM1Eligible(currentTF);  setPoolVisibility(seriesPoolM1Inner, v);  setPoolVisibility(seriesPoolM1Proj, v);  break; }
  }
}

function updateTrendBadge(data) {
  const badge = document.getElementById("trend-badge");
  if (!badge) return;
  if (!data || (!data.h4_trend && !data.h4_potential_trend)) { badge.className = "trend-badge hidden"; return; }
  badge.className = "trend-badge visible";
  const h4Trend  = data.h4_trend || "Neutral";
  const potTrend = data.h4_potential_trend || h4Trend;
  badge.innerHTML = `
    <div class="badge-row"><span class="badge-label">H4 Master:</span><span class="badge-value ${h4Trend.toLowerCase()}">${h4Trend}</span></div>
    <div class="badge-row"><span class="badge-label">Potential:</span><span class="badge-value ${potTrend.toLowerCase()}">${potTrend}</span></div>
  `;
}

let isLoadingStructure = false;

async function loadStructure(symbol, timeframe, count = 1000) {
  if (isLoadingStructure || isLoadingHistory) return;
  if (!structureActive) return;

  let url;
  if (structureMode === "bottomup") {
    url = `${API_BASE}/api/structure_bu?symbol=${symbol}&timeframe=${timeframe}&count=800&pivot_length=${currentPivotLength}`;
  } else {
    url = `${API_BASE}/api/structure?symbol=${symbol}&timeframe=${timeframe}&count=${count}&pivot_length=${currentPivotLength}`;
  }

  if (chart && allCandles && allCandles.length > 0) {
    const lr = chart.timeScale().getVisibleLogicalRange();
    if (lr && lr.from !== null && lr.to !== null) {
      let idxFrom = Math.max(0, Math.min(allCandles.length - 1, Math.floor(lr.from)));
      let idxTo   = Math.max(0, Math.min(allCandles.length - 1, Math.ceil(lr.to)));
      if (!isNaN(idxFrom) && !isNaN(idxTo)) {
        url += `&viewport_start=${allCandles[idxFrom].time}&viewport_end=${allCandles[idxTo].time}`;
      }
    }
  }

  try {
    isLoadingStructure = true;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    drawStructure(data);
  } catch(e) {
    console.error("[Structure] loadStructure error:", e);
  } finally {
    isLoadingStructure = false;
  }
}

async function toggleStructure() {
  const btn        = document.getElementById("structure-btn");
  const layerPanel = document.getElementById("layer-buttons");
  const pivotBox   = document.getElementById("pivot-control-box");
  structureActive  = !structureActive;

  if (structureActive) {
    btn.classList.add("active");
    if (layerPanel) layerPanel.classList.remove("hidden");
    if (pivotBox)   pivotBox.classList.remove("hidden");
    await loadStructure(currentSymbol, currentTF);
  } else {
    btn.classList.remove("active");
    if (layerPanel) layerPanel.classList.add("hidden");
    if (pivotBox)   pivotBox.classList.add("hidden");
    clearStructure();
  }
}

/**
 * Wechselt zwischen Top-Down und Bottom-Up Modus.
 * Wird vom Modus-Toggle-Button in index.html aufgerufen.
 */
function switchStructureMode(mode) {
  if (mode === structureMode) return;
  structureMode = mode;

  // Alle Modus-Buttons updaten
  document.querySelectorAll("[data-struct-mode]").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.structMode === mode);
  });

  // Layer-Panel Sichtbarkeit: Bottom-Up hat keine H4/H1/M15-Toggles
  const layerPanel = document.getElementById("layer-buttons");
  if (layerPanel) {
    layerPanel.querySelectorAll(".layer-btn").forEach(btn => {
      const layer = btn.dataset.layer;
      const isTopDownLayer = ["h4_master", "h1_inner", "m15_inner", "m5_inner", "m1_inner"].includes(layer);
      btn.style.display = (mode === "bottomup" && isTopDownLayer) ? "none" : "";
    });
  }

  // Neu laden
  if (structureActive) loadStructure(currentSymbol, currentTF);
}

// ── App starten ────────────────────────────────────────────────────────────────

async function initApp() {
  const strBtn = document.getElementById("structure-btn");
  if (strBtn) strBtn.addEventListener("click", toggleStructure);

  document.querySelectorAll(".tf-btn").forEach(btn => btn.addEventListener("click", () => switchTF(btn.dataset.tf)));
  document.querySelectorAll(".layer-btn").forEach(btn => btn.addEventListener("click", () => toggleStructureLayer(btn)));

  // Bottom-Up / Top-Down Modus-Toggle
  document.querySelectorAll("[data-struct-mode]").forEach(btn => {
    btn.addEventListener("click", () => switchStructureMode(btn.dataset.structMode));
  });

  const select = document.getElementById("symbol-select");
  if (select) select.addEventListener("change", () => switchSymbol(select.value));

  const pivotSlider  = document.getElementById("pivot-slider");
  const pivotDisplay = document.getElementById("pivot-val-display");
  if (pivotSlider && pivotDisplay) {
    pivotSlider.addEventListener("input",  (e) => { currentPivotLength = parseInt(e.target.value); pivotDisplay.textContent = currentPivotLength; });
    pivotSlider.addEventListener("change", () => { if (structureActive) loadStructure(currentSymbol, currentTF); });
  }

  initChart();
  await loadHistory(currentSymbol, currentTF);
  connectWebSocket(currentSymbol);
  await loadSymbols();
}

document.addEventListener("DOMContentLoaded", initApp);

function toggleVolume() {
  if (!volumeSeries) return;
  const btn = document.getElementById("volume-toggle-btn");
  const nextVisible = !volumeSeries.options().visible;
  volumeSeries.applyOptions({ visible: nextVisible });
  if (btn) btn.classList.toggle("active", nextVisible);
}
