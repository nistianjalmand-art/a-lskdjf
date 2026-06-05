import re

file_path = r"c:\Users\phili\Desktop\Trading - Cowork\trading-dashboard-v4\static\chart.js"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update currentSymbol
content = content.replace('let currentSymbol  = "XAUUSD.s";', 'let currentSymbol  = "XAUUSD";')

# 2. Update Structure-State vars and visibility
state_vars_old = """let structureMicroSeries   = null;   // Lila Micro-Pivot ZigZag       (level=0)
let structureInnerSeries   = null;   // Cyan Inner-Structure ZigZag    (level=2)
let structureTempSeries    = null;   // Slate Temp-Structure ZigZag    (level=3, gestrichelt)
let structureWhiteSeries   = null;   // Weißer ZigZag (BOS-Reversal)
let structureRedSeries     = null;   // Rote Struktur-Linien
let structurePushLines     = [];     // Amber Push-Linien (eine Series pro Segment, level=1)
let structureLowLine       = null;   // Gelbe zone line (confirmed_low)
let structureHighLine      = null;   // Gelbe zone line (confirmed_high)

/** Sichtbarkeit der einzelnen Struktur-Layer (persistiert über TF-Wechsel) */
const layerVisibility = { micro: true, push: true, inner: true, temp: true, zones: true };"""

state_vars_new = """let structureMicroSeries   = null;   // Lila Micro-Pivot ZigZag
let structureH4Series      = null;   // Golden H4 Master Pivots

/** Sichtbarkeit der einzelnen Struktur-Layer (persistiert über TF-Wechsel) */
const layerVisibility = { micro: true, h4_master: true };"""

content = content.replace(state_vars_old, state_vars_new)

# 3. Update clearStructure
clear_old = """function clearStructure() {
  if (structureMicroSeries) {
    try { chart.removeSeries(structureMicroSeries); } catch (_) {}
    structureMicroSeries = null;
  }
  if (structureInnerSeries) {
    try { chart.removeSeries(structureInnerSeries); } catch (_) {}
    structureInnerSeries = null;
  }
  if (structureTempSeries) {
    try { chart.removeSeries(structureTempSeries); } catch (_) {}
    structureTempSeries = null;
  }
  if (structureWhiteSeries) {
    try { chart.removeSeries(structureWhiteSeries); } catch (_) {}
    structureWhiteSeries = null;
  }
  if (structureRedSeries) {
    try { chart.removeSeries(structureRedSeries); } catch (_) {}
    structureRedSeries = null;
  }
  for (const s of structurePushLines) {
    try { chart.removeSeries(s); } catch (_) {}
  }
  structurePushLines = [];
  if (structureLowLine && candleSeries) {
    try { candleSeries.removePriceLine(structureLowLine); } catch (_) {}
    structureLowLine = null;
  }
  if (structureHighLine && candleSeries) {
    try { candleSeries.removePriceLine(structureHighLine); } catch (_) {}
    structureHighLine = null;
  }
  updateTrendBadge(null);
}"""

clear_new = """function clearStructure() {
  if (structureMicroSeries) {
    try { chart.removeSeries(structureMicroSeries); } catch (_) {}
    structureMicroSeries = null;
  }
  if (structureH4Series) {
    try { chart.removeSeries(structureH4Series); } catch (_) {}
    structureH4Series = null;
  }
  updateTrendBadge(null);
}"""

content = content.replace(clear_old, clear_new)

# 4. update drawStructure
old_draw_start = "function drawStructure(data) {"
old_draw_end = "updateTrendBadge(data);\n}"

# We extract everything between old_draw_start and old_draw_end
pattern = re.compile(re.escape(old_draw_start) + r"(.*?)" + re.escape("updateTrendBadge(data);\n}"), re.DOTALL)

draw_new = """function drawStructure(data) {
  lastStructureData = data;   // Für Layer-Toggles ohne Netzwerk-Request cachen
  clearStructure();

  const LineStyle = LightweightCharts.LineStyle;

  // ── 1. Lila Micro-Pivots (lokaler Timeframe) ─────────────────────────
  if (data.micro_pivots && data.micro_pivots.length >= 2) {
    const microMap = new Map();
    for (const p of data.micro_pivots) {
      microMap.set(p.time, pivotToPoint(p));
    }
    const microData = Array.from(microMap.values())
      .sort((a, b) => a.time - b.time);

    structureMicroSeries = chart.addLineSeries({
      color:                  "#d44bec",
      lineWidth:              1,
      priceLineVisible:       false,
      lastValueVisible:       false,
      crosshairMarkerVisible: false,
      lineStyle:              LineStyle.Solid,
    });
    structureMicroSeries.setData(microData);
    structureMicroSeries.applyOptions({ visible: layerVisibility.micro });
  }

  // ── 2. H4 Master Pivots (Goldene Leitplanken) ────────────────────────
  if (data.h4_master_pivots && data.h4_master_pivots.length >= 2) {
    const h4Map = new Map();
    for (const p of data.h4_master_pivots) {
      h4Map.set(p.time, pivotToPoint(p));
    }
    const h4Data = Array.from(h4Map.values())
      .sort((a, b) => a.time - b.time);

    structureH4Series = chart.addLineSeries({
      color:                  "#facc15",
      lineWidth:              3,
      priceLineVisible:       false,
      lastValueVisible:       false,
      crosshairMarkerVisible: false,
      lineStyle:              LineStyle.Solid,
    });
    structureH4Series.setData(h4Data);
    structureH4Series.applyOptions({ visible: layerVisibility.h4_master });
  }

  updateTrendBadge(data);
}"""

content = pattern.sub(draw_new.replace("updateTrendBadge(data);\n}", ""), content)

# 5. update toggleStructureLayer
toggle_old = """switch (layer) {
    case "micro":
      if (structureMicroSeries)
        structureMicroSeries.applyOptions({ visible: layerVisibility.micro });
      break;

    case "push":
      for (const s of structurePushLines)
        s.applyOptions({ visible: layerVisibility.push });
      break;

    case "inner":
      if (structureInnerSeries)
        structureInnerSeries.applyOptions({ visible: layerVisibility.inner });
      break;

    case "temp":
      if (structureTempSeries)
        structureTempSeries.applyOptions({ visible: layerVisibility.temp });
      break;

    case "zones": {
      // PriceLines unterstützen kein applyOptions({visible}) → entfernen & neu anlegen
      if (structureLowLine && candleSeries) {
        try { candleSeries.removePriceLine(structureLowLine);  } catch (_) {}
        structureLowLine = null;
      }
      if (structureHighLine && candleSeries) {
        try { candleSeries.removePriceLine(structureHighLine); } catch (_) {}
        structureHighLine = null;
      }
      if (layerVisibility.zones) {
        if (lastStructureData.confirmed_low) {
          structureLowLine = candleSeries.createPriceLine({
            price: lastStructureData.confirmed_low.price,
            color: "#f59e0b", lineWidth: 1, lineStyle: LineStyle.Dashed,
            axisLabelVisible: true, title: "Low",
          });
        }
        if (lastStructureData.confirmed_high) {
          structureHighLine = candleSeries.createPriceLine({
            price: lastStructureData.confirmed_high.price,
            color: "#f59e0b", lineWidth: 1, lineStyle: LineStyle.Dashed,
            axisLabelVisible: true, title: "High",
          });
        }
      }
      break;
    }
  }"""

toggle_new = """switch (layer) {
    case "micro":
      if (structureMicroSeries)
        structureMicroSeries.applyOptions({ visible: layerVisibility.micro });
      break;

    case "h4_master":
      if (structureH4Series)
        structureH4Series.applyOptions({ visible: layerVisibility.h4_master });
      break;
  }"""

content = content.replace(toggle_old, toggle_new)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
