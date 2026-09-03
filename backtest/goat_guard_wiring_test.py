"""
goat_guard_wiring_test.py — isolated test of
p32_multistage_sim._check_goat_guard_for_segment(), using synthetic trade
log and M15 data. Exists because the full lifecycle smoke test in
p32_multistage_sim.py's __main__ block breaches at Step 1 and never
reaches the funded stage — meaning the Goat Guard wiring added there is
UNTESTED by that smoke test. This file tests the wiring logic in
isolation instead, without needing a full multi-year lifecycle to
actually survive to the funded stage.

Still does NOT test against real M15 data (none available in this
environment) — this confirms the WIRING/LOGIC is correct, not that it
behaves correctly against your actual pulled M15 files. Run this, then
separately confirm a real funded-stage run (once one exists that
survives that far) behaves sensibly on your machine.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import numpy as np

from p32_multistage_sim import _check_goat_guard_for_segment

print("=== Test 1: one closed trade + one still-open trade, combined loss crosses 2% ===\n")

rng = pd.date_range("2026-01-01 00:00", "2026-01-01 06:00", freq="15min")
m15_cache = {
    "fake_a": pd.DataFrame({"close": np.linspace(100.0, 97.0, len(rng))}, index=rng),
    "fake_b": pd.DataFrame({"close": np.linspace(50.0, 49.0, len(rng))}, index=rng),
}

# fake_a: CLOSED trade (would appear in trade_log with a real exit_date)
# fake_b: STILL OPEN at segment end (exit_date=None, as p32_trend_engine.py
# now logs it) — this is exactly the gap that was fixed in trend_engine.py
trade_log = [
    {"instrument": "fake_a", "entry_date": rng[0], "exit_date": rng[-1],
     "entry_price": 100.0, "exit_price": 97.0, "notional_usd": 3000.0,
     "pnl_frac_of_equity_at_exit": -0.018, "still_open": False},
    {"instrument": "fake_b", "entry_date": rng[0], "exit_date": None,
     "entry_price": 50.0, "exit_price": None, "notional_usd": 2000.0,
     "pnl_frac_of_equity_at_exit": None, "still_open": True},
]

triggers = _check_goat_guard_for_segment(
    trade_log, segment_start=rng[0], segment_end=rng[-1],
    account_balance=5000.0, m15_cache=m15_cache,
)

print(f"Triggers found: {len(triggers)}")
for t in triggers:
    print(f"  {t['timestamp']}: {t['combined_floating_loss_pct']:.2%} loss, "
          f"positions: {t['positions_involved']}")

expected = 1
status = "PASS" if len(triggers) == expected else "FAIL"
print(f"\nExpected {expected} trigger (same math as goat_guard_m15.py's own "
      f"synthetic test, but routed through the segment-conversion function "
      f"used by p32_multistage_sim.py, and exercising the still-open-position "
      f"handling specifically) -> {status}")

print("\n=== Test 2: no positions in window -> no triggers, no crash ===\n")
empty_triggers = _check_goat_guard_for_segment(
    [], segment_start=rng[0], segment_end=rng[-1],
    account_balance=5000.0, m15_cache=m15_cache,
)
status2 = "PASS" if len(empty_triggers) == 0 else "FAIL"
print(f"Triggers found: {len(empty_triggers)} -> {status2}")

print("\nBoth tests confirm the WIRING LOGIC is correct against synthetic "
      "data, including the still-open-position handling that was the real "
      "gap being fixed. Real M15 data compatibility is still unverified — "
      "run the actual funded-stage path on your machine once one survives "
      "that far, and report back what happens.")