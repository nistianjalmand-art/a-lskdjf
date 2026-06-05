"""
Struktur-Analyse – Auf reines Micro-Pivot Tracking (Lila) reduziert.
"""
from datetime import datetime
from analysis.models import PivotPoint, StructureState

def update_micro_pivots(
    micro_pivots: list[PivotPoint],
    pivot_time: datetime,
    pivot_price: float,
    is_high: bool,
    tf: str = ""
) -> PivotPoint | None:
    """
    Aktualisiert die Micro-Pivot-Liste (alternierendes High/Low).
    Gibt den neuen/aktualisierten Pivot zurück.
    """
    current_pivot = None

    last_pivot_was_high = False
    if micro_pivots:
        last_pivot_was_high = micro_pivots[-1].is_high

    if is_high:
        if not micro_pivots or not last_pivot_was_high:
            if not micro_pivots or pivot_price > micro_pivots[-1].price:
                current_pivot = PivotPoint(pivot_time, pivot_price, True, tf=tf)
                micro_pivots.append(current_pivot)
        else:
            if micro_pivots and pivot_price > micro_pivots[-1].price:
                current_pivot = PivotPoint(pivot_time, pivot_price, True, tf=tf)
                micro_pivots[-1] = current_pivot
    else:
        if not micro_pivots or last_pivot_was_high:
            if not micro_pivots or pivot_price < micro_pivots[-1].price:
                current_pivot = PivotPoint(pivot_time, pivot_price, False, tf=tf)
                micro_pivots.append(current_pivot)
        else:
            if micro_pivots and pivot_price < micro_pivots[-1].price:
                current_pivot = PivotPoint(pivot_time, pivot_price, False, tf=tf)
                micro_pivots[-1] = current_pivot

    if len(micro_pivots) > 800:
        micro_pivots[:] = micro_pivots[-800:]

    return current_pivot


def filter_alternating_pivots(pivots: list) -> list:
    """
    Filtert eine Liste von Pivot-Punkten so, dass sie strikt alternieren
    (High -> Low -> High ...). Bei zwei aufeinanderfolgenden Hochs/Tiefs
    wird das bessere Extremum (höheres High / tieferes Low) gewählt.
    """
    if not pivots:
        return []

    from analysis.models import PivotPoint
    result = []

    def get_type(p):
        return p.is_high if hasattr(p, 'is_high') else p['is_high']
    def get_price(p):
        return p.price if hasattr(p, 'price') else p['price']
    def make_p(p):
        if hasattr(p, 'time'):
            p.price = round(p.price, 8)
            return p
        from datetime import datetime, timezone
        return PivotPoint(
            time=datetime.fromtimestamp(p['time'], tz=timezone.utc),
            price=round(p['price'], 8),
            is_high=p['is_high'],
            tf=p.get('tf', '')
        )

    for p in pivots:
        p_obj = make_p(p)
        if not result:
            result.append(p_obj)
            continue

        last = result[-1]
        p_is_high = p_obj.is_high
        p_price = p_obj.price
        p_time = int(p_obj.time.timestamp())
        last_time = int(last.time.timestamp())

        if p_time == last_time:
            if p_is_high and last.is_high:
                if p_price > last.price: result[-1] = p_obj
            elif not p_is_high and not last.is_high:
                if p_price < last.price: result[-1] = p_obj
            continue

        if p_is_high == last.is_high:
            if p_is_high:
                if p_price >= last.price:
                    result[-1] = p_obj
            else:
                if p_price <= last.price:
                    result[-1] = p_obj
        else:
            result.append(p_obj)

    return result


def compute_master_structure(micro_pivots: list, stop_time=None) -> tuple:
    """
    Berechnet die Master-Struktur aus einer Liste von Pivot-Punkten.

    Kernprinzip:
      Ein gesamter Trendabschnitt wird als GENAU EIN Swing committed:
        Long: trend_start (erster Tiefpunkt) → letztes HH vor dem BOS
        Short: trend_start (erster Hochpunkt) → letztes LL vor dem BOS

      Committed wird NUR beim BOS (Break of Structure):
        Long-Trend-BOS:  ein Low faellt unter bos_level_price
                         (= tiefster Punkt nach dem letzten HH)
        Short-Trend-BOS: ein High steigt ueber bos_level_price
                         (= hoechster Punkt nach dem letzten LL)

      BOS kann OHNE vorangehendes HH/HL-Muster auftreten:
        Wenn der Preis direkt vom trend_start aus die bos_level bricht,
        wird trotzdem korrekt committed.

    stop_time (datetime | None):
        Wenn gesetzt, werden Pivots NACH diesem Zeitpunkt nicht verarbeitet.
        Dient der H1-Segment-Berechnung.

    Rueckgabe: (confirmed_pivots, [], temp_structure)
    """
    if len(micro_pivots) < 2:
        return [], [], []

    from collections import deque
    from analysis.models import PivotPoint

    confirmed: list      = []
    trend                = 0      # 0=init, 1=long, -1=short
    trend_start          = None   # Erster Punkt des aktuellen Trends
    last_extreme         = None   # Letztes bestaetigtes HH (long) / LL (short)
    bos_level_price      = None   # Preis der aktuellen Struktur-Grenze
    correction_buffer: list = []

    queue = deque(micro_pivots)

    def _make(p):
        return PivotPoint(time=p.time, price=p.price, is_high=p.is_high, tf=getattr(p, 'tf', ''))

    def _commit_swing(start, end):
        """Schreibt einen vollstaendigen Swing (Start -> Ende) in confirmed."""
        if start is None or end is None:
            return
        # Duplikat-Schutz: letzten Punkt nicht doppelt einfuegen
        if not confirmed or confirmed[-1].time != start.time:
            confirmed.append(_make(start))
        confirmed.append(_make(end))

    while queue:
        p = queue.popleft()

        if stop_time is not None and p.time > stop_time:
            break

        # ── Initialisierung: ersten Pivot als Trend-Start setzen ──────────
        if trend == 0:
            trend_start = p
            bos_level_price = p.price
            trend = 1 if not p.is_high else -1
            continue

        # ── SHORT-TREND (sucht LL, bos_level = letztes bestaet. High) ────
        if trend == -1:
            if not p.is_high:
                # Neues LL → Trend setzt sich fort, last_extreme aktualisieren
                if last_extreme is None or p.price < last_extreme.price:
                    last_extreme = p
                    correction_buffer = []
                else:
                    correction_buffer.append(p)
            else:
                # High erscheint im Short-Trend
                correction_buffer.append(p)

                # BOS: High bricht ueber bos_level_price (letztes bestaet. High)
                if p.price > bos_level_price:
                    # Gesamten Short-Swing als EINEN Punkt committen
                    _commit_swing(trend_start, last_extreme)

                    # Trend wechselt zu Long
                    trend           = 1
                    trend_start     = last_extreme   # Naechster Long beginnt am letzten LL
                    bos_level_price = last_extreme.price  # BOS-Level = letztes LL
                    last_extreme    = None
                    buf              = correction_buffer
                    correction_buffer = []
                    # Buffer zurueck in die Queue: moeglicherweise weiteres HH drin
                    queue.extendleft(reversed(buf))
                else:
                    # Kein BOS: Ist dieses High das neue bos_level?
                    # Nein – bos_level bleibt immer das letzte bestaet. High.
                    # Aber wenn noch kein last_extreme existiert, ist dies das
                    # erste High nach trend_start → als potentielles BOS-Level merken.
                    if last_extreme is None:
                        # Noch kein LL bestaetigt: dieses High ist das aktuelle
                        # Ausgangs-Hoch, bos_level updaten
                        bos_level_price = max(bos_level_price, p.price)

        # ── LONG-TREND (sucht HH, bos_level = letztes bestaet. Low) ─────
        elif trend == 1:
            if p.is_high:
                # Neues HH → Trend setzt sich fort, last_extreme aktualisieren
                if last_extreme is None or p.price > last_extreme.price:
                    last_extreme = p
                    correction_buffer = []
                else:
                    correction_buffer.append(p)
            else:
                # Low erscheint im Long-Trend
                correction_buffer.append(p)

                # BOS: Low bricht unter bos_level_price (letztes bestaet. Low)
                if p.price < bos_level_price:
                    # Gesamten Long-Swing als EINEN Punkt committen
                    _commit_swing(trend_start, last_extreme)

                    # Trend wechselt zu Short
                    trend           = -1
                    trend_start     = last_extreme   # Naechster Short beginnt am letzten HH
                    bos_level_price = last_extreme.price  # BOS-Level = letztes HH
                    last_extreme    = None
                    buf              = correction_buffer
                    correction_buffer = []
                    queue.extendleft(reversed(buf))
                else:
                    # Kein BOS: bos_level bleibt das letzte bestaet. Low.
                    # Noch kein HH bestaetigt → bos_level updaten
                    if last_extreme is None:
                        bos_level_price = min(bos_level_price, p.price)

    # Offener Swing am Ende: letzten unabgeschlossenen Trend committen
    if trend_start and last_extreme:
        _commit_swing(trend_start, last_extreme)

    # ── Temp-Struktur: aktueller unbestaetiger Correction-Buffer ─────────
    def _filter_correction(buf: list, last_pt: PivotPoint, correction_is_up: bool) -> list:
        """Filtert den Buffer auf richtungskonsistente Pivots (ZickZack)."""
        if not buf:
            return []

        result = []
        searching_for_high = not last_pt.is_high
        best_candidate = None
        last_high_price = last_pt.price if last_pt.is_high else None
        last_low_price  = last_pt.price if not last_pt.is_high else None

        for pt in buf:
            if correction_is_up:
                if pt.is_high:
                    if last_high_price is not None and pt.price <= last_high_price:
                        continue
                else:
                    if last_low_price is not None and pt.price <= last_low_price:
                        continue
            else:
                if not pt.is_high:
                    if last_low_price is not None and pt.price >= last_low_price:
                        continue
                else:
                    if last_high_price is not None and pt.price >= last_high_price:
                        continue

            if pt.is_high == searching_for_high:
                if best_candidate is None:
                    best_candidate = pt
                else:
                    if correction_is_up:
                        if pt.is_high:
                            if pt.price > best_candidate.price: best_candidate = pt
                        else:
                            if pt.price < best_candidate.price: best_candidate = pt
                    else:
                        if not pt.is_high:
                            if pt.price < best_candidate.price: best_candidate = pt
                        else:
                            if pt.price > best_candidate.price: best_candidate = pt
            else:
                if best_candidate:
                    result.append(best_candidate)
                    if best_candidate.is_high: last_high_price = best_candidate.price
                    else:                      last_low_price  = best_candidate.price
                searching_for_high = pt.is_high
                best_candidate = pt

        if best_candidate:
            result.append(best_candidate)

        return result

    temp_structure = []
    anchor = last_extreme if last_extreme else (confirmed[-1] if confirmed else None)
    if anchor and correction_buffer:
        correction_is_up = (trend == -1)
        filtered = _filter_correction(correction_buffer, anchor, correction_is_up)
        if filtered:
            temp_structure.append(_make(anchor))
            for pt in filtered:
                temp_structure.append(_make(pt))

    return confirmed, [], temp_structure


def compute_inner_zigzag(pivots: list, p_start, p_end) -> list:
    """
    Baut einen detaillierten H1-ZickZack innerhalb eines H4-Segments.
    """
    if not p_start:
        return []

    raw_list = [p_start]
    raw_list.extend(pivots)

    if p_end:
        raw_list.append(p_end)

    filtered = filter_alternating_pivots(raw_list)

    if p_end is not None:
        is_bullish = p_end.price > p_start.price
    else:
        is_bullish = not p_start.is_high

    filtered_trend = _filter_trend_only(filtered, is_bullish)
    return enforce_strict_trend(filtered_trend, is_bullish)


def enforce_strict_trend(pivots: list, is_bullish: bool) -> list:
    """
    Bereinigt die Struktur von vorne nach hinten:
    - Wenn im Downtrend ein HH erscheint, werden alle tieferen Highs
      und deren nachfolgende Lows gelöscht.
    - Das überlebende Low wird auf das absolut tiefste Low des Bereiches gesetzt.
    - Symmetrisch für Uptrend (LL -> Höchstes High retten).
    """
    if len(pivots) < 3:
        return pivots

    from analysis.structure import filter_alternating_pivots

    result = [pivots[0]]

    for i in range(1, len(pivots) - 1):
        p = pivots[i]

        if not is_bullish:
            if p.is_high:
                last_h_idx = -1
                for j in range(len(result)-1, -1, -1):
                    if result[j].is_high:
                        last_h_idx = j
                        break

                if last_h_idx != -1 and p.price > result[last_h_idx].price:
                    to_remove = []
                    for j in range(len(result)-1, 0, -1):
                        if result[j].is_high and result[j].price < p.price:
                            to_remove.append(j)
                            low_after_idx = j + 1
                            if low_after_idx < len(result) and not result[low_after_idx].is_high:
                                to_remove.append(low_after_idx)

                    survivor_idx = -1
                    for j in range(len(result)-1, -1, -1):
                        if not result[j].is_high and j not in to_remove:
                            survivor_idx = j
                            break

                    if survivor_idx != -1:
                        best_low = result[survivor_idx]
                        for idx in to_remove:
                            cand = result[idx]
                            if not cand.is_high and cand.price < best_low.price:
                                best_low = cand
                        if best_low != result[survivor_idx]:
                            survivor = result[survivor_idx]
                            survivor.price = best_low.price
                            survivor.time = best_low.time

                    for idx in sorted(set(to_remove), reverse=True):
                        result.pop(idx)

            result.append(p)

        else:
            if not p.is_high:
                last_l_idx = -1
                for j in range(len(result)-1, -1, -1):
                    if not result[j].is_high:
                        last_l_idx = j
                        break

                if last_l_idx != -1 and p.price < result[last_l_idx].price:
                    to_remove = []
                    for j in range(len(result)-1, 0, -1):
                        if not result[j].is_high and result[j].price > p.price:
                            to_remove.append(j)
                            high_after_idx = j + 1
                            if high_after_idx < len(result) and result[high_after_idx].is_high:
                                to_remove.append(high_after_idx)

                    survivor_idx = -1
                    for j in range(len(result)-1, -1, -1):
                        if result[j].is_high and j not in to_remove:
                            survivor_idx = j
                            break

                    if survivor_idx != -1:
                        best_high = result[survivor_idx]
                        for idx in to_remove:
                            cand = result[idx]
                            if cand.is_high and cand.price > best_high.price:
                                best_high = cand
                        if best_high != result[survivor_idx]:
                            survivor = result[survivor_idx]
                            survivor.price = best_high.price
                            survivor.time = best_high.time

                    for idx in sorted(set(to_remove), reverse=True):
                        result.pop(idx)

            result.append(p)

    result.append(pivots[-1])
    return filter_alternating_pivots(result)


def _filter_trend_only(pivots: list, is_bullish: bool) -> list:
    """
    Sammelt alle wichtigen Punkte für die Struktur:
    - HHs und HLs (Rausch-Filterung)
    - Erhält Anomalien (LL im Uptrend / HH im Downtrend)
    """
    if len(pivots) < 2:
        return pivots

    result = [pivots[0]]
    searching_high = not pivots[0].is_high
    last_h = pivots[0] if pivots[0].is_high else None
    last_l = pivots[0] if not pivots[0].is_high else None
    best_candidate = None
    epsilon = 0.0001

    for p in pivots[1:]:
        if is_bullish:
            if searching_high:
                if p.is_high:
                    if best_candidate is None or p.price > best_candidate.price:
                        best_candidate = p
                else:
                    if last_l and p.price < (last_l.price - epsilon):
                        if best_candidate: result.append(best_candidate)
                        result.append(p)
                        last_l = p
                        best_candidate = None
                        searching_high = True
                        continue
                    if best_candidate:
                        result.append(best_candidate)
                        last_h = best_candidate
                        searching_high = False
                        best_candidate = p
            else:
                if not p.is_high:
                    if last_l and p.price < (last_l.price - epsilon):
                        result.append(p)
                        last_l = p
                        best_candidate = None
                        searching_high = True
                        continue
                    if best_candidate is None or p.price < best_candidate.price:
                        best_candidate = p
                else:
                    if last_h and p.price > (last_h.price + epsilon):
                        if best_candidate: result.append(best_candidate)
                        best_candidate = p
                        searching_high = True
        else:
            if not searching_high:
                if not p.is_high:
                    if best_candidate is None or p.price < best_candidate.price:
                        best_candidate = p
                else:
                    if last_h and p.price > (last_h.price + epsilon):
                        if best_candidate: result.append(best_candidate)
                        result.append(p)
                        last_h = p
                        best_candidate = None
                        searching_high = False
                        continue
                    if best_candidate:
                        result.append(best_candidate)
                        last_l = best_candidate
                        searching_high = True
                        best_candidate = p
            else:
                if p.is_high:
                    if last_h and p.price > (last_h.price + epsilon):
                        result.append(p)
                        last_h = p
                        best_candidate = None
                        searching_high = False
                        continue
                    if best_candidate is None or p.price > best_candidate.price:
                        best_candidate = p
                else:
                    if last_l and p.price < (last_l.price - epsilon):
                        if best_candidate: result.append(best_candidate)
                        best_candidate = p
                        searching_high = False

    if best_candidate and best_candidate not in result:
        result.append(best_candidate)

    if pivots[-1] not in result:
        result.append(pivots[-1])

    return filter_alternating_pivots(result)
