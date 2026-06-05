import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from datetime import datetime, timezone
from analysis.models import PivotPoint
from analysis.structure import compute_inner_zigzag

def make_p(time_sec, price, is_high):
    return PivotPoint(
        time=datetime.fromtimestamp(time_sec, tz=timezone.utc),
        price=price,
        is_high=is_high
    )

def test_bearish_strict():
    print("\n--- Test Bearish (Downtrend) Strict Logic ---")
    p_start = make_p(1000, 100, True) # Start: High
    p_end   = make_p(2000, 50, False) # Ende: Low (Downtrend)
    
    # Micro Pivots: L1, H1, L2, H2(HH), L3
    pivots = [
        make_p(1100, 80, False), # L1
        make_p(1200, 90, True),  # H1 (LH)
        make_p(1300, 75, False), # L2 (LL)
        make_p(1400, 95, True),  # H2 (HH! > H1)
        make_p(1500, 70, False), # L3 (LL)
    ]
    
    # Erwartet: H0, L1, H1, L2, H2, L3 ... -> filter_trend_only -> H0, L1, H1, L2, H2, L3, L_end
    # Dann enforce_strict_trend:
    # H2 is HH compared to H1.
    # H1 is deleted.
    # Low after H1 (L2) is kept because it's the last low before H2.
    # Result: H0, L1, L2, H2, L3, L_end
    # After alternating filter: H0, L2 (min of L1, L2), H2, L_end
    
    result = compute_inner_zigzag(pivots, p_start, p_end)
    
    print("Bearish ZigZag result:")
    for p in result:
        print(f"  {'H' if p.is_high else 'L'} @ {p.price} ({p.time.timestamp()})")

    # Check that H1 (90) is gone
    prices = [p.price for p in result]
    assert 90 not in prices, "H1 should have been deleted"
    print("OK: H1 deleted.")

def test_bullish_strict():
    print("\n--- Test Bullish (Uptrend) Strict Logic ---")
    p_start = make_p(1000, 50, False) # Start: Low
    p_end   = make_p(2000, 100, True) # Ende: High (Uptrend)
    
    # Micro Pivots: H1, L1, H2, L2(LL), H3
    pivots = [
        make_p(1100, 70, True),  # H1
        make_p(1200, 60, False), # L1 (HL)
        make_p(1300, 80, True),  # H2 (HH)
        make_p(1400, 55, False), # L2 (LL! < L1)
        make_p(1500, 90, True),  # H3 (HH)
    ]
    
    # Erwartet: L0, H1, L1, H2, L2, H3, H_end
    # L2 is LL compared to L1.
    # L1 is deleted.
    # High after L1 (H2) is kept because it's the last high before L2.
    # Result: L0, H1, H2, L2, H3, H_end
    # After alternating filter: L0, H2 (max of H1, H2), L2, H3, H_end
    
    result = compute_inner_zigzag(pivots, p_start, p_end)
    
    print("Bullish ZigZag result:")
    for p in result:
        print(f"  {'H' if p.is_high else 'L'} @ {p.price} ({p.time.timestamp()})")

    prices = [p.price for p in result]
    assert 60 not in prices, "L1 should have been deleted"
    print("OK: L1 deleted.")

if __name__ == "__main__":
    test_bearish_strict()
    test_bullish_strict()
    print("\nAll tests passed!")
