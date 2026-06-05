# M15 Inner Structure Changelog

Dokumentation der Änderungen für die Implementierung der M15-Innenstruktur (Aqua) innerhalb der H1-Struktur.

## 2026-04-20 - Initialisierung & Planung

- [x] Kick-off des Projekts: H4 -> H1 -> M15 Verschachtelung.
- [x] Implementierungsplan erstellt: Fokus auf "Bulk Viewport Loading" zur Performance-Optimierung.
- [x] `task.md` initialisiert.

---

## Geplante Änderungen am Backend

### analysis/engine.py
- [ ] `_load_viewport_candles`: Neue Funktion zum massenweisen Laden von Kerzen für das gesamte Sichtfeld.
- [ ] `_compute_nested_paths`: Generalisierung der H1-Logik, um sie rekursiv auf M15 anwenden zu können.
- [ ] `get_smart_structure`: Integration der M15-Ebene (aktiv nur wenn TF <= 15m).

### main.py
- [ ] Rückgabe-Schema erweitern um `m15_inner_structure`.

---

## Geplante Änderungen am Frontend

### index.html / style.css
- [ ] M15 Layer-Button hinzugefügt.
- [ ] Farb-Variable `--m15-aqua` definiert.

### chart.js
- [ ] Neue Linie `structureM15Series` initialisiert.
- [ ] `drawStructure` Rendering-Logik für 3 Ebenen angepasst.
