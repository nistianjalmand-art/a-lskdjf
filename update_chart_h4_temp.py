import re

file_path = r"c:\Users\phili\Desktop\Trading - Cowork\trading-dashboard-v4\static\chart.js"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. State update
content = content.replace("let structureH4Series      = null;   // Golden H4 Master Pivots",
                           "let structureH4Series      = null;   // Golden H4 Master Pivots\nlet structureH4TempSeries  = null;   // Dashed Yellow H4 Temp Pivots")

# 2. clearStructure update
clear_old = """  if (structureH4Series) {
    try { chart.removeSeries(structureH4Series); } catch (_) {}
    structureH4Series = null;
  }"""
clear_new = """  if (structureH4Series) {
    try { chart.removeSeries(structureH4Series); } catch (_) {}
    structureH4Series = null;
  }
  if (structureH4TempSeries) {
    try { chart.removeSeries(structureH4TempSeries); } catch (_) {}
    structureH4TempSeries = null;
  }"""
content = content.replace(clear_old, clear_new)

# 3. drawStructure update
draw_h4 = """  // ── 2. H4 Master Pivots (Goldene Leitplanken) ────────────────────────
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
  }"""

draw_h4_new = """  // ── 2. H4 Master Pivots (Goldene Leitplanken) ────────────────────────
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

  // ── 3. H4 Temp Pivots (Gestrichelte Gelbe Linien) ──────────────────────
  if (data.h4_temp_pivots && data.h4_temp_pivots.length >= 2) {
    const h4TempMap = new Map();
    for (const p of data.h4_temp_pivots) {
      h4TempMap.set(p.time, pivotToPoint(p));
    }
    const h4TempData = Array.from(h4TempMap.values())
      .sort((a, b) => a.time - b.time);

    structureH4TempSeries = chart.addLineSeries({
      color:                  "#facc15",
      lineWidth:              2,
      priceLineVisible:       false,
      lastValueVisible:       false,
      crosshairMarkerVisible: false,
      lineStyle:              LineStyle.Dashed,
    });
    structureH4TempSeries.setData(h4TempData);
    structureH4TempSeries.applyOptions({ visible: layerVisibility.h4_master });
  }"""
content = content.replace(draw_h4, draw_h4_new)

# 4. toggleStructureLayer update
toggle_old = """    case "h4_master":
      if (structureH4Series)
        structureH4Series.applyOptions({ visible: layerVisibility.h4_master });
      break;"""
toggle_new = """    case "h4_master":
      if (structureH4Series)
        structureH4Series.applyOptions({ visible: layerVisibility.h4_master });
      if (structureH4TempSeries)
        structureH4TempSeries.applyOptions({ visible: layerVisibility.h4_master });
      break;"""
content = content.replace(toggle_old, toggle_new)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)
