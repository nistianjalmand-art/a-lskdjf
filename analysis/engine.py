"""
StructureEngine – V5 (Top-Down H4 + H1 Inner Structure).

Berechnet:
  1. Lokale Micro-Pivots (aktueller Timeframe, lila)
  2. H4 Master-Struktur (Goldene Linie)
  3. H4 Temp-Struktur (Gestrichelte Gelbe Linie)
  4. H1 Inner Structure (Hellgrün) – segment-begrenzt für jedes H4-Paar
     → Smart-Loading-Engine: lädt via copy_rates_range exakt die Kerzen
       des jeweiligen H4-Segments, basierend auf dem Viewport-Fenster.
"""
import time
from datetime import datetime, timezone
from loguru import logger

from analysis.models import StructureState, dicts_to_candles, PivotPoint
from analysis.pivot import compute_all_pivots, detect_pivot_high, detect_pivot_low
from analysis.structure import update_micro_pivots, compute_master_structure, filter_alternating_pivots, compute_inner_zigzag

# Pivot-Länge je Timeframe
TF_PIVOT_LENGTHS: dict[str, int] = {
    "1m":  2,
    "5m":  2,
    "15m": 2,
    "30m": 2,
    "1h":  2,
    "4h":  2,
    "1d":  2,
}

# Maximale Anzahl H4-Segmente, die gleichzeitig H1-berechnet werden
MAX_H1_SEGMENTS = 8

# Cache-TTL in Sekunden
CACHE_TTL_SECONDS = 300  # 5 Minuten

# Timeframe in Sekunden (für Snap-Fenster)
TF_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# Threshold: M5 inner structure wird nur berechnet wenn TF <= 5m
M5_ELIGIBLE_SECONDS = 300

# Threshold: M15 inner structure wird nur berechnet wenn TF <= 15m
M15_ELIGIBLE_SECONDS = 900

# Threshold: M1 inner structure wird nur berechnet wenn TF <= 1m
M1_ELIGIBLE_SECONDS = 60



class StructureEngine:
    def __init__(self, pivot_length: int = 2) -> None:
        self.pivot_length = pivot_length

        # Cache-Struktur:
        #   _mtf_cache[key] = {"time": float, "result": <payload>}
        # Keys:
        #   f"{symbol}_4h"                     → (h4_master_pivots, h4_temp_pivots)
        #   f"{symbol}_1h_{seg_start_unix}"    → list[dict]  (H1 candles für Segment)
        self._mtf_cache: dict[str, dict] = {}

    # ─────────────────────────────────────────────────────────────────────────
    # Öffentliche Hilfsmethode: Micro-Pivots aus Kerzen berechnen
    # ─────────────────────────────────────────────────────────────────────────

    def compute_pivots_for_candles(self, timeframe: str, candles: list, tf_label: str = "", override_length: int = None) -> list:
        """Berechnet Micro-Pivots für eine gegebene Kerzen-Liste."""
        if not candles:
            return []

        # Priorität: 1. override_length, 2. TF_PIVOT_LENGTHS, 3. self.pivot_length
        pivot_length = override_length if override_length is not None else TF_PIVOT_LENGTHS.get(timeframe, self.pivot_length)
        state = StructureState()

        max_i = len(candles) - pivot_length - 1
        for i in range(max_i + 1):
            candle = candles[i]
            ph = detect_pivot_high(candles, pivot_length, i)
            pl = detect_pivot_low(candles, pivot_length, i)

            handle_as_high, handle_as_low = ph is not None, pl is not None
            if ph is not None and pl is not None:
                if state.micro_pivots and state.micro_pivots[-1].is_high:
                    handle_as_high, handle_as_low = False, True
                else:
                    handle_as_high, handle_as_low = True, False

            if handle_as_high:
                update_micro_pivots(state.micro_pivots, candle.time, ph, True, tf_label)
            elif handle_as_low:
                update_micro_pivots(state.micro_pivots, candle.time, pl, False, tf_label)

        return state.micro_pivots

    # ─────────────────────────────────────────────────────────────────────────
    # UNIVERSAL PIVOT SNAPPING (Standard Pattern)
    # ─────────────────────────────────────────────────────────────────────────
    # IMPORTANT: "Extremum-Snap" is the mandatory standard for all cross-timeframe
    # structure layers (H4 Master, H1 Inner, etc.).
    # Whenever a higher-TF pivot is projected onto a lower-TF chart, it MUST be
    # snapped to the exact sub-candle that reached the extreme high/low.
    # This ensures visual accuracy and mathematical consistency.
    # ─────────────────────────────────────────────────────────────────────────

    def _snap_pivots(self, pivots: list, fine_candles: list, source_tf: str) -> list:

        """
        Verschiebt den Timestamp von H4-Pivots auf die exakte Sub-Kerze,
        die das Extremum erreicht hat.
        """
        if not pivots or not fine_candles:
            return pivots

        window_size = TF_SECONDS.get(source_tf, 14400)
        snapped = []
        
        # Kerzen-Zeiten für Index-Suche
        candle_times = [c.time.timestamp() for c in fine_candles]
        import bisect

        for p in pivots:
            p_ts = p.time.timestamp() if hasattr(p, 'time') else p['time']
            is_high = p.is_high if hasattr(p, 'is_high') else p['is_high']
            
            # Da die Master-Pivots auf Broker-Kerzen basieren, ist p.time bereits der perfekte Anker.
            # Wir verzichten auf mathematisches UTC-Rounding (// window_size), da dieses 
            # bei Broker-Timezone-Offsets (z.B. UTC+2/3) das Suchfenster falsch verschieben würde.
            anchor_ts = int(p_ts)
            
            start_idx = bisect.bisect_left(candle_times, anchor_ts)
            end_idx = bisect.bisect_left(candle_times, anchor_ts + window_size)
            
            sub_candles = fine_candles[start_idx:end_idx]

            if not sub_candles:
                snapped.append(p)
                continue
                
            if is_high:
                best = max(sub_candles, key=lambda c: c.high)
                best_price = round(best.high, 8)
            else:
                best = min(sub_candles, key=lambda c: c.low)
                best_price = round(best.low, 8)
            
            # Update time UND Preis (für pixel-perfect Snapping ohne horizontale 'Schultern')
            if hasattr(p, 'time'):
                from analysis.models import PivotPoint
                new_p = PivotPoint(
                    time=best.time, 
                    price=best_price, 
                    is_high=p.is_high, 
                    tf=getattr(p, 'tf', '')
                )
                snapped.append(new_p)
            else:
                new_p = p.copy()
                new_p['time'] = int(best.time.timestamp())
                new_p['time_iso'] = best.time.isoformat()
                new_p['price'] = best_price
                snapped.append(new_p)
                
        return snapped



    # ─────────────────────────────────────────────────────────────────────────
    # Interne Cache-Hilfsmethoden
    # ─────────────────────────────────────────────────────────────────────────

    def _cache_get(self, key: str):
        """Gibt den gecachten Wert zurück, wenn noch gültig. Sonst None."""
        entry = self._mtf_cache.get(key)
        if entry and (time.time() - entry["time"]) < CACHE_TTL_SECONDS:
            return entry["result"]
        return None

    def _cache_set(self, key: str, result) -> None:
        """Speichert einen Wert im Cache mit aktuellem Timestamp."""
        self._mtf_cache[key] = {"time": time.time(), "result": result}

    # ─────────────────────────────────────────────────────────────────────────
    # H4-Segment-Extraktion: Pivot-Paare aus der Master-Struktur
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_parent_segments(
        self,
        master_path: list,
        temp_path: list,
        now_ts: float,
    ) -> list[tuple]:
        """
        Extrahiert Segmente (Start, Ende) aus einer Pfad-Liste.
        Unterstützt Master + Temp Pfade (wichtig für H4).
        """
        all_pivots = list(master_path)
        if temp_path:
            for tp in temp_path:
                last_ts = all_pivots[-1].time.timestamp() if all_pivots else 0
                t_ts = tp.time.timestamp() if hasattr(tp, 'time') else tp['time']
                if t_ts > last_ts:
                    if isinstance(tp, dict):
                        from analysis.models import PivotPoint
                        from datetime import datetime, timezone
                        new_tp = PivotPoint(
                            time=datetime.fromtimestamp(tp['time'], tz=timezone.utc),
                            price=tp['price'],
                            is_high=tp['is_high'],
                            tf=tp.get('tf', '')
                        )
                        all_pivots.append(new_tp)
                    else:
                        all_pivots.append(tp)

        if not all_pivots:
            return []

        segments = []
        for i in range(len(all_pivots) - 1):
            a = all_pivots[i]
            b = all_pivots[i + 1]
            segments.append((a, b, b.time.timestamp()))

        # Letztes offenes Segment bis 'jetzt'
        last = all_pivots[-1]
        segments.append((last, None, now_ts))
        return segments

    async def _load_bulk_candles(
        self,
        symbol: str,
        timeframe: str,
        start_ts: float,
        end_ts: float,
    ) -> list[dict]:
        """
        Lädt Kerzen massenweise für einen Bereich.
        Wird genutzt um N API-Anfragen pro Viewport zu vermeiden.
        """
        from metaapi_client import metaapi
        
        tf_sec = TF_SECONDS.get(timeframe, 3600)
        start_rounded = int(start_ts // tf_sec) * tf_sec
        end_rounded   = int(end_ts   // tf_sec) * tf_sec
        cache_key = f"{symbol}_bulk_{timeframe}_{start_rounded}_{end_rounded}"

        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        from_dt = datetime.fromtimestamp(start_ts - tf_sec * 5, tz=timezone.utc)
        to_dt   = datetime.fromtimestamp(end_ts   + tf_sec * 5, tz=timezone.utc)

        candles = await metaapi.get_historical_candles_range(symbol, timeframe, from_dt, to_dt)
        if candles:
            self._cache_set(cache_key, candles)
        return candles

    async def _compute_sub_structure(
        self,
        symbol: str,
        parent_segments: list[tuple],
        child_tf: str,
        viewport_start: int,
        viewport_end: int,
        pivot_length: int = None,
        parent_tf_label: str = "4h"
    ) -> list:
        """
        Berechnet eine feinere Struktur innerhalb einer Liste von Eltern-Segmenten.
        """
        if not parent_segments:
            return []

        now_ts = time.time()
        relevant = []
        for (p_start, p_end, seg_end) in parent_segments:
            seg_start = p_start.time.timestamp()
            effective_end = seg_end if seg_end else now_ts
            
            # In Viewport:
            in_viewport = (effective_end >= viewport_start and seg_start <= viewport_end)
            
            # Live Edge Override: Wenn der Nutzer in die Zukunft scrollt ("zu weit vorne"),
            # aber nicht in der Vergangenheit ist (viewport_end >= seg_start),
            # behalten wir die letzten Segmente bei, damit die projizierten Strukturen sichtbar bleiben.
            is_recent = (effective_end >= now_ts - 86400 * 3) # Endet in den letzten 3 Tagen
            
            if in_viewport or (is_recent and viewport_end >= seg_start):
                relevant.append((p_start, p_end, seg_start, seg_end or now_ts))

        if not relevant:
            return []

        # Budget: H1 darf mehr als M15/M5 (weil weniger Segmente vorliegen)
        seg_budget = {"1h": 15, "15m": 30, "5m": 60, "1m": 100}
        max_segs = seg_budget.get(child_tf, 30)
        if len(relevant) > max_segs:
            relevant = relevant[-max_segs:]

        # Bulk Load
        bulk_start = min(s[2] for s in relevant)
        bulk_end   = max(s[3] for s in relevant)
        all_child_candles_raw = await self._load_bulk_candles(symbol, child_tf, bulk_start, bulk_end)
        
        if not all_child_candles_raw:
            return []
            
        all_child_candles = dicts_to_candles(all_child_candles_raw)
        candle_ts = [c.time.timestamp() for c in all_child_candles]
        
        import bisect
        all_segments_result = []

        for (p_start, p_end, seg_start, seg_end) in relevant:
            idx_start = bisect.bisect_left(candle_ts, seg_start)
            idx_end   = bisect.bisect_right(candle_ts, seg_end)
            seg_candles = all_child_candles[idx_start:idx_end]

            if len(seg_candles) < 4:
                continue

            child_micro = self.compute_pivots_for_candles(child_tf, seg_candles, child_tf, override_length=pivot_length)
            if not child_micro:
                continue

            # Snapping
            snapped_start_list = self._snap_pivots([p_start], seg_candles, parent_tf_label)
            snapped_end_list = []
            if p_end:
                snapped_end_list = self._snap_pivots([p_end], seg_candles, parent_tf_label)
                
            s_start = snapped_start_list[0] if snapped_start_list else p_start
            s_end   = snapped_end_list[0] if snapped_end_list else p_end

            s_ts_start = s_start.time.timestamp()
            s_ts_end   = s_end.time.timestamp() if p_end else seg_end
            
            filtered_child_micro = [
                p for p in child_micro
                if s_ts_start < p.time.timestamp() < s_ts_end
            ]
            
            # ZigZag — Trend-Richtung aus dem Parent-Segment ableiten
            # Wenn kein Endpunkt (offenes Segment): Richtung aus p_start.is_high lesen.
            # Ein High als Start bedeutet: der vorherige Schritt endete im High,
            # also geht der Push nach UNTEN (bearish).
            # Ein Low als Start bedeutet: der vorherige Schritt endete im Low,
            # also geht der Push nach OBEN (bullish).
            inner_zigzag = compute_inner_zigzag(filtered_child_micro, s_start, s_end)
            all_segments_result.extend(inner_zigzag)

        if not all_segments_result:
            return []

        all_segments_result.sort(key=lambda x: x.time)
        return filter_alternating_pivots(all_segments_result)

    def _build_projected_path(self, last_pt, micro_pivots, candles_raw, local_candles, source_tf) -> list[dict]:
        """
        Baut einen Pfad von 'last_pt' über alle folgenden Micro-Pivots
        bis hin zum aktuellen Preis-Extremum (Projektion).
        Nutzt die smarte compute_inner_zigzag Logik für Cleanup und Trend-Filterung.
        """
        if not last_pt:
            return []

        last_pt_ts = last_pt.time.timestamp()
        
        # 1. Zwischen-Pivots finden
        tail_pivots = [p for p in micro_pivots if p.time.timestamp() > last_pt_ts]
        
        # 2. Endpunkt (Projektion) finden
        p_end = None
        if candles_raw:
            # Wir suchen das absolute Extremum seit dem letzten Pivot im Pfad
            ref_pt = tail_pivots[-1] if tail_pivots else last_pt
            new_candles = [c for c in candles_raw if c["time"] > ref_pt.time.timestamp()]
            if new_candles:
                # Welches Extremum suchen wir als nächstes? Alternierend zum letzten.
                if ref_pt.is_high:
                    best = min(new_candles, key=lambda c: c["low"])
                    proj_p, is_h = best["low"], False
                else:
                    best = max(new_candles, key=lambda c: c["high"])
                    proj_p, is_h = best["high"], True
                
                p_end = PivotPoint(
                    time=datetime.fromtimestamp(best["time"], tz=timezone.utc),
                    price=proj_p,
                    is_high=is_h
                )

        # 3. Den Pfad mit der smarten ZickZack-Logik berechnen (inkls. Cleanup!)
        # compute_inner_zigzag macht filter_alternating_pivots + enforce_strict_trend
        smart_path = compute_inner_zigzag(tail_pivots, last_pt, p_end)

        # 4. Den gesamten Pfad snappen
        if len(smart_path) < 2:
            return []

        snapped = self._snap_pivots(smart_path, local_candles, source_tf)
        
        # LWC erfordert strikt aufsteigende Timestamps.
        final_dicts = []
        for p in snapped:
            d = p.to_dict() if hasattr(p, "to_dict") else p
            if not final_dicts or d["time"] > final_dicts[-1]["time"]:
                final_dicts.append(d)
                
        return final_dicts


    async def get_smart_structure(
        self,
        symbol: str,
        timeframe: str,
        viewport_start: int,
        viewport_end: int,
        count: int,
        pivot_length: int = None,
    ) -> dict:
        """
        Lädt und berechnet alle Struktur-Layer für das aktuelle Viewport-Fenster.
        """
        from metaapi_client import metaapi
        now_ts = time.time()

        # --- Hilfsfunktionen für Segment-Filtering ---
        def get_segs(pts):
            """Extrahiert (start_tuple, end_tuple) aus einer Punkt-Liste."""
            s = set()
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i+1]
                t1 = p1['time'] if isinstance(p1, dict) else int(p1.time.timestamp())
                v1 = round(p1['price'] if isinstance(p1, dict) else p1.price, 8)
                t2 = p2['time'] if isinstance(p2, dict) else int(p2.time.timestamp())
                v2 = round(p2['price'] if isinstance(p2, dict) else p2.price, 8)
                s.add(((t1, v1), (t2, v2)))
            return s

        def filter_to_subpaths(pts, forbidden):
            """Teilt einen Pfad in Sub-Pfade auf, indem überlappende Segmente entfernt werden."""
            if not pts: return []
            paths = []
            curr = []
            for i in range(len(pts) - 1):
                p1, p2 = pts[i], pts[i+1]
                t1 = p1['time'] if isinstance(p1, dict) else int(p1.time.timestamp())
                v1 = round(p1['price'] if isinstance(p1, dict) else p1.price, 8)
                t2 = p2['time'] if isinstance(p2, dict) else int(p2.time.timestamp())
                v2 = round(p2['price'] if isinstance(p2, dict) else p2.price, 8)

                if ((t1, v1), (t2, v2)) in forbidden:
                    if curr:
                        paths.append(curr)
                        curr = []
                else:
                    d1 = p1.to_dict() if hasattr(p1, "to_dict") else p1
                    d2 = p2.to_dict() if hasattr(p2, "to_dict") else p2
                    if not curr:
                        curr.append(d1)
                    # Nur hinzufügen, wenn Zeitstempel aufsteigend (Deduplizierung für LWC)
                    if d2['time'] > curr[-1]['time']:
                        curr.append(d2)
                    elif d2['time'] == curr[-1]['time'] and d2['price'] != curr[-1]['price']:
                        # Bei gleichem Zeitstempel nehmen wir das extremere Level
                        if (curr[-1]['is_high'] and d2['price'] > curr[-1]['price']) or \
                           (not curr[-1]['is_high'] and d2['price'] < curr[-1]['price']):
                            curr[-1]['price'] = d2['price']
            if curr:
                paths.append(curr)
            return paths

        # ── 1. H4 Master Daten (Cache) ────────────────────────────────────────
        h4_candles_raw = await metaapi.get_historical_candles(symbol, "4h", 500)
        h4_latest_ts   = h4_candles_raw[-1]["time"] if h4_candles_raw else 0
        
        h4_cache_key = f"h4_{symbol}_{h4_latest_ts}"
        h4_master = []
        h4_temp   = []
        filtered_h4_pivots = []

        cached_h4 = self._cache_get(h4_cache_key)
        if cached_h4 is not None:
            h4_master, _, h4_temp, filtered_h4_pivots = cached_h4
        else:
            if h4_candles_raw:
                p_cands = dicts_to_candles(h4_candles_raw)
                raw_pivots_list = self.compute_pivots_for_candles("4h", p_cands, "4h")
                filtered_h4_pivots = filter_alternating_pivots(raw_pivots_list)
                h4_master, h4_inner_skipped, h4_temp = compute_master_structure(filtered_h4_pivots)
                self._cache_set(h4_cache_key, (h4_master, h4_inner_skipped, h4_temp, filtered_h4_pivots))

        # ── 2. Lokale Daten laden ─────────────────────────────────────────────
        local_candles_raw = await metaapi.get_historical_candles(symbol, timeframe, count)
        local_candles = dicts_to_candles(local_candles_raw) if local_candles_raw else []
        
        # ── 3. H4 Sichtbarkeit & Snapping ─────────────────────────────────────
        snapped_master = self._snap_pivots(list(h4_master), local_candles, "4h")
        snapped_temp   = self._snap_pivots(list(h4_temp), local_candles, "4h")

        vp_pad = 86400 * 5
        visible_h4_master = []
        for p in snapped_master:
            t = p.time.timestamp()
            in_viewport = (viewport_start - vp_pad) <= t <= (viewport_end + vp_pad)
            is_recent = (t >= now_ts - 86400 * 14) # H4 Punkte können weiter zurückliegen (14 Tage)
            if in_viewport or (is_recent and viewport_end >= t):
                visible_h4_master.append(p.to_dict())

        visible_h4_temp = []
        for p in snapped_temp:
            t = p.time.timestamp()
            in_viewport = (viewport_start - vp_pad) <= t <= (viewport_end + vp_pad)
            is_recent = (t >= now_ts - 86400 * 14)
            if in_viewport or (is_recent and viewport_end >= t):
                visible_h4_temp.append(p.to_dict())

        # ── 3.1 H4 Projected (Gepunktet - MULTI SEGMENT) ─────────────────────
        visible_h4_projected = []
        if snapped_master or snapped_temp:
            last_pt = snapped_temp[-1] if snapped_temp else snapped_master[-1]
            visible_h4_projected = self._build_projected_path(
                last_pt, filtered_h4_pivots, h4_candles_raw, local_candles, "4h"
            )

        # ── 4. Verschachtelte Pfade (H1 & M15) ────────────────────────────────
        h1_inner_path = []
        m15_inner_path = []
        m5_inner_path = []
        m1_inner_path = []
        h1_raw_micro = []
        m15_raw_micro = []
        m5_raw_micro = []
        m1_raw_micro = []
        h1_proj = []
        m15_proj = []
        m5_proj = []
        m1_proj = []

        if viewport_start > 0 and h4_master:
            h4_combined_temp = list(h4_temp) + visible_h4_projected
            h4_segments = self._extract_parent_segments(h4_master, h4_combined_temp, now_ts)
            h1_inner_path = await self._compute_sub_structure(
                symbol, h4_segments, "1h", viewport_start, viewport_end, pivot_length, "4h"
            )
            # Für die Projektion brauchen wir die neuesten H1-Pivots
            h1_c_raw = await metaapi.get_historical_candles(symbol, "1h", 100)
            if h1_c_raw:
                p_cands = dicts_to_candles(h1_c_raw)
                h1_raw_micro = filter_alternating_pivots(self.compute_pivots_for_candles("1h", p_cands, "1h"))

            if h1_inner_path:
                h1_snapped_full = self._snap_pivots(h1_inner_path, local_candles, "1h")
                if h1_snapped_full:
                    h1_proj = self._build_projected_path(
                        h1_snapped_full[-1], h1_raw_micro, h1_c_raw if 'h1_c_raw' in locals() else [], 
                        local_candles, "1h"
                    )

            is_low_tf = TF_SECONDS.get(timeframe, 3600) <= M15_ELIGIBLE_SECONDS
            if h1_inner_path and is_low_tf:
                h1_segments = self._extract_parent_segments(h1_inner_path, h1_proj, now_ts)
                m15_inner_path = await self._compute_sub_structure(
                    symbol, h1_segments, "15m", viewport_start, viewport_end, pivot_length, "1h"
                )
                m15_c_raw = await metaapi.get_historical_candles(symbol, "15m", 150)
                if m15_c_raw:
                    p_cands = dicts_to_candles(m15_c_raw)
                    m15_raw_micro = filter_alternating_pivots(self.compute_pivots_for_candles("15m", p_cands, "15m"))

                if m15_inner_path:
                    m15_snapped_full = self._snap_pivots(m15_inner_path, local_candles, "15m")
                    if m15_snapped_full:
                        m15_proj = self._build_projected_path(
                            m15_snapped_full[-1], m15_raw_micro, m15_c_raw if 'm15_c_raw' in locals() else [], 
                            local_candles, "15m"
                        )

        # ── 4b. M5 Inner Structure (nur bei TF <= 5m) ──────────────────────────
        is_m5_tf = TF_SECONDS.get(timeframe, 3600) <= M5_ELIGIBLE_SECONDS
        if is_m5_tf and m15_inner_path:
            m15_segments = self._extract_parent_segments(m15_inner_path, m15_proj, now_ts)
            m5_inner_path = await self._compute_sub_structure(
                symbol, m15_segments, "5m", viewport_start, viewport_end, pivot_length, "15m"
            )
            m5_c_raw = await metaapi.get_historical_candles(symbol, "5m", 400)
            if m5_c_raw:
                p_cands = dicts_to_candles(m5_c_raw)
                m5_raw_micro = filter_alternating_pivots(self.compute_pivots_for_candles("5m", p_cands, "5m"))

            if m5_inner_path:
                m5_snapped_full = self._snap_pivots(m5_inner_path, local_candles, "5m")
                if m5_snapped_full:
                    m5_proj = self._build_projected_path(
                        m5_snapped_full[-1], m5_raw_micro, m5_c_raw if 'm5_c_raw' in locals() else [],
                        local_candles, "5m"
                    )

        # ── 4c. M1 Inner Structure (nur bei TF <= 1m) ──────────────────────────
        is_m1_tf = TF_SECONDS.get(timeframe, 3600) <= M1_ELIGIBLE_SECONDS
        if is_m1_tf and m5_inner_path:
            m5_segments = self._extract_parent_segments(m5_inner_path, m5_proj, now_ts)
            m1_inner_path = await self._compute_sub_structure(
                symbol, m5_segments, "1m", viewport_start, viewport_end, pivot_length, "5m"
            )
            m1_c_raw = await metaapi.get_historical_candles(symbol, "1m", 600)
            if m1_c_raw:
                p_cands = dicts_to_candles(m1_c_raw)
                m1_raw_micro = filter_alternating_pivots(self.compute_pivots_for_candles("1m", p_cands, "1m"))

            if m1_inner_path:
                m1_snapped_full = self._snap_pivots(m1_inner_path, local_candles, "1m")
                if m1_snapped_full:
                    m1_proj = self._build_projected_path(
                        m1_snapped_full[-1], m1_raw_micro, m1_c_raw if 'm1_c_raw' in locals() else [],
                        local_candles, "1m"
                    )

        # ── 5. Snapping & Projektionen für das Frontend ───────────────────────────
        # Hierarchisches Filtering:
        # 1. H4 Master ist die Basis (immer sichtbar)
        # 2. H4 Temp wird gegen H4 Master gefiltert
        # 3. H4 Projected gegen H4 Master + H4 Temp
        # 4. H1 Inner gegen H4 Master + H4 Temp + H4 Projected
        # 5. H1 Projected gegen H1 Inner + H4
        # 6. M15 Inner gegen H1 + H4
        # 7. M15 Projected gegen M15 Inner + H1 + H4

        h4_master_dicts = [p.to_dict() for p in snapped_master]
        h4_m_segs = get_segs(h4_master_dicts)

        # Filter H4 Temp
        h4_temp_paths = filter_to_subpaths(snapped_temp, h4_m_segs)
        h4_t_segs = get_segs([p for path in h4_temp_paths for p in path])
        
        # Filter H4 Projected
        h4_p_segs_all = h4_m_segs | h4_t_segs
        h4_proj_paths = filter_to_subpaths(visible_h4_projected, h4_p_segs_all)
        h4_proj_segs = get_segs([p for path in h4_proj_paths for p in path])

        # H4 Gesamt-Basis für H1
        h4_all_segs = h4_m_segs | h4_t_segs | h4_proj_segs

        # ── 5.1 H1 Inner & Projection ────────────────────────────────────────
        h1_inner_paths = filter_to_subpaths(h1_inner_path, h4_all_segs)
        h1_final = []
        if h1_inner_paths:
            for path_pts in h1_inner_paths:
                snapped = self._snap_pivots(path_pts, local_candles, "1h")
                h1_final.append([p if isinstance(p, dict) else p.to_dict() for p in snapped])

        h1_all_segs_list = get_segs([p for path in h1_final for p in path])
        h1_base_segs = h4_all_segs | h1_all_segs_list

        # Filter H1 Projected
        h1_proj_paths = filter_to_subpaths(h1_proj, h1_base_segs)
        h1_proj_final = []
        for path_pts in h1_proj_paths:
            snapped = self._snap_pivots(path_pts, local_candles, "1h")
            h1_proj_final.append([p if isinstance(p, dict) else p.to_dict() for p in snapped])

        # ── 5.2 M15 Inner & Projection ───────────────────────────────────────
        m15_base_segs = h1_base_segs | get_segs([p for path in h1_proj_final for p in path])
        m15_inner_paths = filter_to_subpaths(m15_inner_path, m15_base_segs)
        m15_final = []
        if m15_inner_paths:
            for path_pts in m15_inner_paths:
                snapped = self._snap_pivots(path_pts, local_candles, "15m")
                m15_final.append([p if isinstance(p, dict) else p.to_dict() for p in snapped])

        m15_all_segs_list = get_segs([p for path in m15_final for p in path])
        m15_proj_base_segs = m15_base_segs | m15_all_segs_list

        # Filter M15 Projected
        m15_proj_paths = filter_to_subpaths(m15_proj, m15_proj_base_segs)
        m15_proj_final = []
        for path_pts in m15_proj_paths:
            snapped = self._snap_pivots(path_pts, local_candles, "15m")
            m15_proj_final.append([p if isinstance(p, dict) else p.to_dict() for p in snapped])

        # ── 5.3 M5 Inner & Projection ─────────────────────────────────────────
        m5_final = []
        m5_proj_final = []
        if is_m5_tf:
            m5_base_segs = m15_proj_base_segs | get_segs([p for path in m15_proj_final for p in path])
            m5_inner_paths = filter_to_subpaths(m5_inner_path, m5_base_segs)
            if m5_inner_paths:
                for path_pts in m5_inner_paths:
                    snapped = self._snap_pivots(path_pts, local_candles, "5m")
                    m5_final.append([p if isinstance(p, dict) else p.to_dict() for p in snapped])

            m5_all_segs = get_segs([p for path in m5_final for p in path])
            m5_proj_base = m5_base_segs | m5_all_segs
            m5_proj_paths = filter_to_subpaths(m5_proj, m5_proj_base)
            for path_pts in m5_proj_paths:
                snapped = self._snap_pivots(path_pts, local_candles, "5m")
                m5_proj_final.append([p if isinstance(p, dict) else p.to_dict() for p in snapped])

        # ── 5.4 M1 Inner & Projection ─────────────────────────────────────────
        m1_final = []
        m1_proj_final = []
        if is_m1_tf:
            m1_base_segs = m5_proj_base | get_segs([p for path in m5_proj_final for p in path])
            m1_inner_paths = filter_to_subpaths(m1_inner_path, m1_base_segs)
            if m1_inner_paths:
                for path_pts in m1_inner_paths:
                    snapped = self._snap_pivots(path_pts, local_candles, "1m")
                    m1_final.append([p if isinstance(p, dict) else p.to_dict() for p in snapped])

            m1_all_segs = get_segs([p for path in m1_final for p in path])
            m1_proj_base = m1_base_segs | m1_all_segs
            m1_proj_paths = filter_to_subpaths(m1_proj, m1_proj_base)
            for path_pts in m1_proj_paths:
                snapped = self._snap_pivots(path_pts, local_candles, "1m")
                m1_proj_final.append([p if isinstance(p, dict) else p.to_dict() for p in snapped])

        # ── 6. Micro-Pivots ───────────────────────────────────────────────────
        local_pivots_obj = self.compute_pivots_for_candles(timeframe, local_candles, timeframe, override_length=pivot_length)
        local_pivots = [p.to_dict() for p in local_pivots_obj]

        # ── 7. Trend-Berechnung (Vereinfacht) ──────────────────────────────────
        def get_trend_str(path):
            if len(path) < 2: return "Neutral"
            last = path[-1]
            prev = path[-2]
            lp = last['price'] if isinstance(last, dict) else last.price
            pp = prev['price'] if isinstance(prev, dict) else prev.price
            return "Bullish" if lp > pp else "Bearish"

        h4_trend = get_trend_str(snapped_master)
        h4_potential = get_trend_str(visible_h4_projected)

        return {
            "symbol":               symbol,
            "timeframe":            timeframe,
            "h4_trend":             h4_trend,
            "h4_potential_trend":   h4_potential,
            "micro_pivots":         local_pivots,
            "h4_master_pivots":     h4_master_dicts,
            "h4_temp_pivots":       h4_temp_paths,
            "h4_projected_pivots":  h4_proj_paths,
            "h1_inner_structure":   h1_final,
            "h1_projected_pivots":  h1_proj_final,
            "m15_inner_structure":  m15_final,
            "m15_projected_pivots": m15_proj_final,
            "m5_inner_structure":   m5_final,
            "m5_projected_pivots":  m5_proj_final,
            "m1_inner_structure":   m1_final,
            "m1_projected_pivots":  m1_proj_final,
        }
