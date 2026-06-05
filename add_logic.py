import sys
from datetime import datetime

file_path = r"c:\Users\phili\Desktop\Trading - Cowork\trading-dashboard-v4\analysis\structure.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

new_logic = """
def compute_master_structure(micro_pivots: list) -> tuple:
    \"\"\"
    Berechnet die Master-Struktur (Goldene Linie) und die noch unbestätigte
    'Temp-Struktur' (Gestrichelte Gelbe Linien = Correction Buffer).
    \"\"\"
    if len(micro_pivots) < 2:
        return [], []

    from collections import deque
    from analysis.models import PivotPoint

    level1: list           = []
    trend                  = 0     # 0=init, 1=long, -1=short
    bos_level_price        = None
    impulse_start          = None
    highest_since_ll       = None
    lowest_since_hh        = None
    last_confirmed_extreme = None
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
                    if highest_since_ll:
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
                    if lowest_since_hh:
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

    # Der aktuelle unbestätigte correction_buffer wird als Temp-Structure ausgegeben (inklusive dem Startpunkt)
    temp_structure = []
    if last_confirmed_extreme and correction_buffer:
        temp_structure.append(_make(last_confirmed_extreme))
        for p in correction_buffer:
            temp_structure.append(_make(p))

    return level1, temp_structure
"""

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content + "\n" + new_logic)
