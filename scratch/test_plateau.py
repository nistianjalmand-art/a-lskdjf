
import sys
import os
from datetime import datetime

sys.path.append(os.getcwd())
from analysis.models import PivotPoint
from analysis.structure import compute_inner_zigzag

def test_plateau():
    print("Testing for Horizontal Plateaus...")
    
    # H4 High (Start) snapped to 10:00
    s_start = PivotPoint(datetime(2026, 1, 1, 10, 0), 100.0, True)
    
    # H4 Low (End) snapped to 14:00
    s_end = PivotPoint(datetime(2026, 1, 1, 14, 0), 20.0, False)
    
    # H1 Pivots between them
    # Angenommen es gibt ein zweites High zum selben Preis um 11:00
    pivots = [
        PivotPoint(datetime(2026, 1, 1, 11, 0), 100.0, True),  # Gleicher Preis wie Start!
        PivotPoint(datetime(2026, 1, 1, 12, 0), 50.0, False),
        PivotPoint(datetime(2026, 1, 1, 13, 0), 70.0, True),
    ]
    
    zigzag = compute_inner_zigzag(pivots, s_start, s_end)
    
    print("\n--- Resulting Zigzag ---")
    for p in zigzag:
        print(f"Point: {p.time} | {p.price} ({'High' if p.is_high else 'Low'})")

    # Check für aufeinanderfolgende Punkte mit gleichem Preis
    for i in range(len(zigzag)-1):
        if zigzag[i].price == zigzag[i+1].price:
            print(f"WARNING: Horizontal line detected between indices {i} and {i+1}!")

if __name__ == "__main__":
    test_plateau()
