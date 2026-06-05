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
            # Neuer High-Pivot (nach einem Low)
            if not micro_pivots or pivot_price > micro_pivots[-1].price:
                current_pivot = PivotPoint(pivot_time, pivot_price, True, tf=tf)
                micro_pivots.append(current_pivot)
        else:
            # Update: höheres High
            if micro_pivots and pivot_price > micro_pivots[-1].price:
                current_pivot = PivotPoint(pivot_time, pivot_price, True, tf=tf)
                micro_pivots[-1] = current_pivot
    else:  # Low
        if not micro_pivots or last_pivot_was_high:
            # Neuer Low-Pivot (nach einem High)
            if not micro_pivots or pivot_price < micro_pivots[-1].price:
                current_pivot = PivotPoint(pivot_time, pivot_price, False, tf=tf)
                micro_pivots.append(current_pivot)
        else:
            # Update: tieferes Low
            if micro_pivots and pivot_price < micro_pivots[-1].price:
                current_pivot = PivotPoint(pivot_time, pivot_price, False, tf=tf)
                micro_pivots[-1] = current_pivot

    # Array-Größe begrenzen
    if len(micro_pivots) > 800:
        micro_pivots[:] = micro_pivots[-800:]

    return current_pivot


def filter_alternating_pivots(pivots: list) -> list:
    """
    Kernelement der 'Lila Logik': 
    Filtert eine Liste von Pivot-Punkten so, dass sie strikt alternieren 
    (High -> Low -> High ...). Bei zwei aufeinanderfolgenden Hochs/Tiefs 
    wird das bessere Extremum (höheres High / tieferes Low) gewählt.
    """
    if not pivots:
        return []

    from analysis.models import PivotPoint
    result = []
    
    # Hilfsfunktion für Pivot-Typen
    def get_type(p):
        return p.is_high if hasattr(p, 'is_high') else p['is_high']
    def get_price(p):
        return p.price if hasattr(p, 'price') else p['price']
    def make_p(p):
        if hasattr(p, 'time'): 
            # Preis runden um Floating-Point-Artefakte zu vermeiden
            p.price = round(p.price, 8)
            return p
        # Konvertierung falls Dict (für Frontend-Kompatibilität in Engine)
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

        # NEU: Identische Timestamps abfangen
        if p_time == last_time:
            # Bei Zeitkollision: immer das extremere Extremum behalten
            if p_is_high and last.is_high:
                if p_price > last.price: result[-1] = p_obj
            elif not p_is_high and not last.is_high:
                if p_price < last.price: result[-1] = p_obj
            else:
                # Unterschiedliche Typen zur gleichen Zeit? 
                # Das ist oft ein Spike (High+Low in einer Kerze).
                # Wir nehmen das, was weiter vom vorherigen Punkt (result[-2]) weg ist 
                # oder bleiben einfach beim ersten.
                pass
            continue

        if p_is_high == last.is_high:
            # Gleicher Typ: Besseres Extremum behalten
            if p_is_high: # High
                if p_price >= last.price:
                    result[-1] = p_obj
            else: # Low
                if p_price <= last.price:
                    result[-1] = p_obj
        else:
            # Typwechsel
            # Optional: Wenn Preisunterschied minimal (< 1 Pip), könnte man ignorieren?
            # Aber wir bleiben bei der reinen Lehre, da Snapping nun gefixt ist.
            result.append(p_obj)
            
    return result



def compute_master_structure(micro_pivots: list, stop_time=None) -> tuple:
    """
    Berechnet die Master-Struktur (Goldene Linie) und die noch unbestätigte
    'Temp-Struktur' (Gestrichelte Gelbe Linien = Correction Buffer).

    stop_time (datetime | None):
        Wenn gesetzt, werden Pivots NACH diesem Zeitpunkt nicht mehr verarbeitet
        und die Struktur wird am Ende des Segments hart abgeschlossen.
        Dient der H1-Segment-Berechnung (harter Reset an H4-Pivot-Grenzen).
    """
    if len(micro_pivots) < 2:
        return [], [], []

    from collections import deque
    from analysis.models import PivotPoint

    level1: list           = []
    trend                  = 0     # 0=init, 1=long, -1=short
    bos_level_price        = None
    impulse_start          = None
    highest_since_ll       = None
    lowest_since_hh        = None
    last_confirmed_extreme = None
    inner_structure: list    = []   # level=2: Inner structure (if implemented)
    correction_buffer: list = []

    MAX_REPLAYS    = 50
    queue          = deque(micro_pivots)

    def _make(p):
        return PivotPoint(time=p.time, price=p.price, is_high=p.is_high, tf=getattr(p, 'tf', ''))

    def _commit(first, extreme):
        if first is None or extreme is None:
            return
        if not level1 or level1[-1].time != first.time:
            level1.append(_make(first))
        level1.append(_make(extreme))

    while queue:
        p = queue.popleft()

        # stop_time-Grenze: Keine Pivots nach dem Segment-Ende verarbeiten (H1-Modus)
        if stop_time is not None and p.time > stop_time:
            break

        replay_count = 0
        if trend == 0:
            if p.is_high:
                impulse_start   = p
                bos_level_price = p.price
                trend           = -1
            else:
                impulse_start   = p
                bos_level_price = p.price
                trend           = 1
            continue

        if trend == -1:
            if not p.is_high:
                if last_confirmed_extreme is None or p.price < last_confirmed_extreme.price:
                    # Neues LL im Short-Trend
                    if highest_since_ll is not None:
                        # Eine Korrektur war aktiv: Push 1 + Korrektur als eigene Segmente
                        prev_ll = last_confirmed_extreme
                        _commit(impulse_start, prev_ll)                  # Push 1
                        _commit(prev_ll, highest_since_ll)                # Korrektur
                        impulse_start = highest_since_ll                  # Push 2 startet hier
                        bos_level_price = highest_since_ll.price
                    last_confirmed_extreme = p
                    highest_since_ll       = None
                    correction_buffer      = []
                else:
                    correction_buffer.append(p)
            else:
                if highest_since_ll is None or p.price > highest_since_ll.price:
                    highest_since_ll = p
                correction_buffer.append(p)
                if p.price > bos_level_price:
                    _commit(impulse_start, last_confirmed_extreme)
                    trend                  = 1
                    impulse_start          = last_confirmed_extreme
                    bos_level_price        = last_confirmed_extreme.price
                    last_confirmed_extreme = None
                    lowest_since_hh        = None
                    buf               = correction_buffer
                    correction_buffer = []
                    queue.extendleft(reversed(buf))

        elif trend == 1:
            if p.is_high:
                if last_confirmed_extreme is None or p.price > last_confirmed_extreme.price:
                    # Neues HH im Long-Trend
                    if lowest_since_hh is not None:
                        # Eine Korrektur war aktiv: Push 1 + Korrektur als eigene Segmente
                        prev_hh = last_confirmed_extreme
                        _commit(impulse_start, prev_hh)                  # Push 1
                        _commit(prev_hh, lowest_since_hh)                # Korrektur
                        impulse_start = lowest_since_hh                  # Push 2 startet hier
                        bos_level_price = lowest_since_hh.price
                    last_confirmed_extreme = p
                    lowest_since_hh        = None
                    correction_buffer      = []
                else:
                    correction_buffer.append(p)
            else:
                if lowest_since_hh is None or p.price < lowest_since_hh.price:
                    lowest_since_hh = p
                correction_buffer.append(p)
                if p.price < bos_level_price:
                    _commit(impulse_start, last_confirmed_extreme)
                    trend                  = -1
                    impulse_start          = last_confirmed_extreme
                    bos_level_price        = last_confirmed_extreme.price
                    last_confirmed_extreme = None
                    highest_since_ll       = None
                    buf               = correction_buffer
                    correction_buffer = []
                    queue.extendleft(reversed(buf))

    if impulse_start and last_confirmed_extreme:
        _commit(impulse_start, last_confirmed_extreme)

    # Der aktuelle unbestätigte correction_buffer wird als Temp-Structure ausgegeben.
    # Dabei werden nur Pivots angezeigt, die der Korrektur-Richtung entsprechen:
    #   Short-Trend → Long-Korrektur  → nur HH + HL (kein LH)
    #   Long-Trend  → Short-Korrektur → nur LL + LH (kein HL/HH)
    # Zusätzlich wird ein ZickZack erzwungen (keine zwei Highs/Lows nacheinander).
    def _filter_correction(buf: list, last_pt: PivotPoint, correction_is_up: bool) -> list:
        """Filtert den Buffer auf richtungskonsistente Pivots und erzwingt ZickZack."""
        if not buf: return []
        
        result = []
        # Wir suchen zuerst den entgegengesetzten Typ des letzten bestätigten Extremums
        searching_for_high = not last_pt.is_high 
        best_candidate = None
        
        # Um HH/HL bzw. LL/LH zu prüfen, merken wir uns die Preise der akzeptierten Pivots
        last_high_price = last_pt.price if last_pt.is_high else None
        last_low_price  = last_pt.price if not last_pt.is_high else None

        for pt in buf:
            # 1. Trend-Validierung (muss HH/HL bzw. LL/LH sein)
            if correction_is_up:
                if pt.is_high:
                    if last_high_price is not None and pt.price <= last_high_price: continue
                else:
                    if last_low_price is not None and pt.price <= last_low_price: continue
            else:
                if not pt.is_high:
                    if last_low_price is not None and pt.price >= last_low_price: continue
                else:
                    if last_high_price is not None and pt.price >= last_high_price: continue

            # 2. ZickZack-Logik: Alternierende Typen finden
            if pt.is_high == searching_for_high:
                # Gleicher Typ wie das aktuelle Ziel: Besten Kandidaten wählen
                if best_candidate is None:
                    best_candidate = pt
                else:
                    if correction_is_up:
                        if pt.is_high: # HH: Höchstes nehmen
                            if pt.price > best_candidate.price: best_candidate = pt
                        else:          # HL: Das tiefere der Lows nehmen (laut User-Wunsch)
                            if pt.price < best_candidate.price: best_candidate = pt
                    else:
                        if not pt.is_high: # LL: Tiefstes nehmen
                            if pt.price < best_candidate.price: best_candidate = pt
                        else:              # LH: Das höhere der Highs nehmen
                            if pt.price > best_candidate.price: best_candidate = pt
            else:
                # Typwechsel! Besten Kandidaten des vorherigen Typs einloggen
                if best_candidate:
                    result.append(best_candidate)
                    if best_candidate.is_high: last_high_price = best_candidate.price
                    else:                      last_low_price  = best_candidate.price
                    
                    # Jetzt suchen wir den Typ, den wir gerade gesehen haben
                    searching_for_high = pt.is_high
                    best_candidate = pt
        
        # Letzten Kandidaten hinzufügen
        if best_candidate:
            result.append(best_candidate)
            
        return result

    temp_structure = []
    if last_confirmed_extreme and correction_buffer:
        # trend == -1 (Short) → Korrektur geht Long (aufwärts) → HH+HL
        correction_is_up = (trend == -1)
        filtered = _filter_correction(correction_buffer, last_confirmed_extreme, correction_is_up)
        if filtered:
            temp_structure.append(_make(last_confirmed_extreme))
            for p in filtered:
                temp_structure.append(_make(p))

    return level1, inner_structure, temp_structure


def compute_inner_zigzag(pivots: list, p_start, p_end) -> list:
    """
    Baut einen detaillierten H1-ZickZack innerhalb eines H4-Segments.
    - Kombiniert H4-Startpunkt, H1-Micro-Pivots und H4-Endpunkt.
    - Wendet die 'Lila Logik' (filter_alternating_pivots) an.
    - Dies stellt sicher, dass alle Punkte inkl. Anfang/Ende sauber verbunden sind.
    """
    if not p_start:
        return []

    # Liste zusammenbauen: H4-Start + H1-Pivots (+ H4-Ende)
    raw_list = [p_start]
    raw_list.extend(pivots)
    
    if p_end:
        raw_list.append(p_end)
        
    filtered = filter_alternating_pivots(raw_list)
    
    # JETZT: Trend-Filterung (Nur HH/HL bei Up-Push, LL/LH bei Down-Push)
    # Wenn p_end vorhanden: Richtung aus Preisdifferenz ableiten.
    # Wenn p_end None (offenes Segment): Richtung aus p_start.is_high:
    #   - p_start.is_high == True  → vorige Struktur endete oben → Push geht nach UNTEN (bearish)
    #   - p_start.is_high == False → vorige Struktur endete unten → Push geht nach OBEN (bullish)
    if p_end is not None:
        is_bullish = p_end.price > p_start.price
    else:
        is_bullish = not p_start.is_high  # High-Start → Bearish-Push; Low-Start → Bullish-Push
    
    # 1. Smarter Vor-Filter (Rausch-Filterung bei Erhalt von Anomalien)
    filtered_trend = _filter_trend_only(filtered, is_bullish)
    
    # 2. Die "Lösch-Logik" + Pivot-Umpacken bei Trend-Anomalien
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
        
        if not is_bullish: # Bearish (Downtrend)
            if p.is_high:
                # Suche nach HH Anomaly
                last_h_idx = -1
                for j in range(len(result)-1, -1, -1):
                    if result[j].is_high:
                        last_h_idx = j
                        break
                
                if last_h_idx != -1 and p.price > result[last_h_idx].price:
                    to_remove = []
                    
                    # 1. Sammeln was gelöscht werden muss
                    for j in range(len(result)-1, 0, -1):
                        if result[j].is_high and result[j].price < p.price:
                            to_remove.append(j)
                            low_after_idx = j + 1
                            if low_after_idx < len(result) and not result[low_after_idx].is_high:
                                to_remove.append(low_after_idx)
                    
                    # 2. Den "wahren Überlebenden" finden (das letzte Low, das NICHT gelöscht wird)
                    survivor_idx = -1
                    for j in range(len(result)-1, -1, -1):
                        if not result[j].is_high and j not in to_remove:
                            survivor_idx = j
                            break
                    
                    # 3. Das absolut tiefste Low aus der Löschzone finden
                    if survivor_idx != -1:
                        best_low = result[survivor_idx]
                        for idx in to_remove:
                            cand = result[idx]
                            if not cand.is_high and cand.price < best_low.price:
                                best_low = cand
                        
                        # Survivor auf das absolute Extremum umpacken
                        if best_low != result[survivor_idx]:
                            survivor = result[survivor_idx]
                            survivor.price = best_low.price
                            survivor.time = best_low.time

                    # 4. Löschen
                    for idx in sorted(set(to_remove), reverse=True):
                        result.pop(idx)
            
            result.append(p)
            
        else: # Bullish (Uptrend)
            if not p.is_high:
                # Suche nach LL Anomaly
                last_l_idx = -1
                for j in range(len(result)-1, -1, -1):
                    if not result[j].is_high:
                        last_l_idx = j
                        break
                
                if last_l_idx != -1 and p.price < result[last_l_idx].price:
                    to_remove = []
                    
                    # 1. Sammeln
                    for j in range(len(result)-1, 0, -1):
                        if not result[j].is_high and result[j].price > p.price:
                            to_remove.append(j)
                            high_after_idx = j + 1
                            if high_after_idx < len(result) and result[high_after_idx].is_high:
                                to_remove.append(high_after_idx)
                    
                    # 2. Den Survivor finden (das letzte High, das NICHT gelöscht wird)
                    survivor_idx = -1
                    for j in range(len(result)-1, -1, -1):
                        if result[j].is_high and j not in to_remove:
                            survivor_idx = j
                            break
                    
                    # 3. Das absolut höchste High finden
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

                    # 4. Löschen
                    for idx in sorted(set(to_remove), reverse=True):
                        result.pop(idx)
            
            result.append(p)

    result.append(pivots[-1])
    return filter_alternating_pivots(result)


def _filter_trend_only(pivots: list, is_bullish: bool) -> list:
    """
    Sammelt alle wichtigen Punkte für die Struktur:
    - HHs und HLs (Rausch-Filterung)
    - ABER: Erhält Anomalien (LL im Uptrend / HH im Downtrend)
    - Damit die iterative Rollback-Logik (enforce_strict_trend) triggern kann.
    """
    if len(pivots) < 2: return pivots
    
    result = [pivots[0]] 
    searching_high = not pivots[0].is_high
    last_h = pivots[0] if pivots[0].is_high else None
    last_l = pivots[0] if not pivots[0].is_high else None
    best_candidate = None
    
    # Toleranz für Gold (0.1 Pips = 0.01 oder 0.0001 je nach Broker, wir nutzen 0.0001 als Standard)
    epsilon = 0.0001 

    for p in pivots[1:]:
        if is_bullish:
            if searching_high:
                if p.is_high:
                    if best_candidate is None or p.price > best_candidate.price:
                        best_candidate = p
                else: # p.is_low
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
            else: # searching_low (HL)
                if not p.is_high:
                    if last_l and p.price < (last_l.price - epsilon):
                        result.append(p)
                        last_l = p
                        best_candidate = None
                        searching_high = True
                        continue
                    if best_candidate is None or p.price < best_candidate.price:
                        best_candidate = p
                else: # p.is_high
                    if last_h and p.price > (last_h.price + epsilon):
                        if best_candidate: result.append(best_candidate)
                        best_candidate = p
                        searching_high = True
        else: # Bearish (Downtrend)
            if not searching_high: # searching_low (LL)
                if not p.is_high:
                    if best_candidate is None or p.price < best_candidate.price:
                        best_candidate = p
                else: # p.is_high (Anomalie HH?)
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
            else: # searching_high (LH)
                if p.is_high:
                    if last_h and p.price > (last_h.price + epsilon):
                        result.append(p)
                        last_h = p
                        best_candidate = None
                        searching_high = False
                        continue
                    if best_candidate is None or p.price > best_candidate.price:
                        best_candidate = p
                else: # p.is_low
                    if last_l and p.price < (last_l.price - epsilon):
                        if best_candidate: result.append(best_candidate)
                        best_candidate = p
                        searching_high = False

    if best_candidate and best_candidate not in result: result.append(best_candidate)
    
    # Sicherstellen, dass das Ende immer dabei ist
    if pivots[-1] not in result:
        result.append(pivots[-1])
        
    return filter_alternating_pivots(result)
