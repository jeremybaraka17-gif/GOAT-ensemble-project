"""
goat_guard_m15.py — reconstructs combined floating P&L across all open
positions at M15 resolution, to detect Goat Guard triggers (2% of
account balance, funded stage only). See research/decisions_log.md,
"Goat Guard modeling method: INTRABAR (M15)" for why this was chosen
over daily high/low (false-synchronization risk across instruments).

HONEST STATUS: this file's core function (check_positions_against_m15)
is built and tested here against SYNTHETIC data only — I don't have
access to your actual M15 CSVs to test against. Real M15 data structure
should match exactly (same columns as the D1 files: time, open, high,
low, close, ...), so this should work directly, but it has NOT been
verified against your real pulled data. Run the __main__ block below
against your actual data/raw/*_M15.csv files before trusting this for
anything beyond structure.

STILL NOT WIRED IN: this function is a standalone utility. Integrating
it into p32_multistage_sim.py's funded-stage segment loop requires that
loop to expose per-day position state (entry price, notional, direction)
as it runs — currently p32_trend_engine.py's simulate_portfolio() only
returns an aggregate daily return series, not a position log. That
wiring is the next concrete task, not done here — flagged explicitly so
it isn't mistaken for already-integrated.

METHOD: for a given date range and a log of positions open during that
range (from the D1-resolution funded-stage simulation), loads the M15
bars covering that range for each open instrument, and at each M15
timestamp computes combined floating P&L across all simultaneously-open
positions using each bar's CLOSE (a reasonable per-15-minute mark,
not attempting to resolve sub-bar spikes — see the known, stated
limitation in decisions_log.md: a spike-and-recover fully inside one
M15 bar could trigger Goat Guard in live reality without showing up
here. Small, one-directional understatement, preferred over daily-HL's
large overstatement from false synchronization).
"""
import os
import pandas as pd
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "raw")

GOAT_GUARD_THRESHOLD_PCT = 0.02  # 2% of account balance — confirmed, decisions_log.md


def load_m15(name: str) -> pd.DataFrame:
    path = os.path.join(DATA_DIR, f"{name}_M15.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. This function needs the M15 CSVs pulled "
            f"earlier via pull_m15_history.py — confirm they're in data/raw/."
        )
    df = pd.read_csv(path, parse_dates=["time"])
    return df.set_index("time").sort_index()[["close"]]


def check_positions_against_m15(positions: list, account_balance: float,
                                  m15_data: dict = None) -> dict:
    """
    positions: list of dicts, each {"instrument": str, "entry_price": float,
        "notional_usd": float, "start_date": pd.Timestamp, "end_date": pd.Timestamp}
        — describes one position's lifespan (entry to exit) during the
        window being checked. Multiple positions can overlap in time.

    account_balance: the account balance to check the 2% threshold
        against. Real GFT rule uses current balance, not a fixed
        starting value — caller is responsible for passing the right
        figure for whatever moment is being checked (this function
        doesn't track balance evolution itself).

    m15_data: optional pre-loaded {instrument: DataFrame} dict, to avoid
        reloading CSVs repeatedly in a loop. If None, loads on demand via
        load_m15() for whichever instruments appear in `positions`.

    Returns: {"triggers": [list of trigger events, each with timestamp,
        combined_floating_loss_pct, positions_involved], "trigger_count": int}
        Caller (multistage sim) is responsible for translating trigger
        COUNT into the actual soft-then-hard Goat Guard consequence
        (1st = split cut, 2nd = account closed) — this function only
        detects threshold crossings, matching GFT's own snapshot-based
        re-trigger rule (a fixed position set fluctuating below the
        threshold doesn't re-trigger — only a NEW crossing does).
    """
    if not positions:
        return {"triggers": [], "trigger_count": 0}

    instruments_needed = sorted(set(p["instrument"] for p in positions))
    if m15_data is None:
        m15_data = {inst: load_m15(inst) for inst in instruments_needed}

    window_start = min(p["start_date"] for p in positions)
    window_end = max(p["end_date"] for p in positions)

    # Build a combined timeline of all M15 timestamps across instruments
    # in this window
    all_timestamps = sorted(set().union(*[
        m15_data[inst].loc[window_start:window_end].index for inst in instruments_needed
    ]))

    triggers = []
    already_triggered_for_current_position_set = False
    last_position_set_signature = None

    for ts in all_timestamps:
        # positions actually open at this exact timestamp
        open_now = [p for p in positions if p["start_date"] <= ts <= p["end_date"]]
        if not open_now:
            continue

        position_set_signature = tuple(sorted((p["instrument"], p["start_date"]) for p in open_now))
        if position_set_signature != last_position_set_signature:
            # the open position set changed (new entry or exit) — per
            # GFT's own rule, a NEW position set can trigger again even
            # if the previous set had already triggered
            already_triggered_for_current_position_set = False
            last_position_set_signature = position_set_signature

        if already_triggered_for_current_position_set:
            continue

        combined_floating_pnl = 0.0
        for p in open_now:
            inst = p["instrument"]
            if ts not in m15_data[inst].index:
                continue
            current_price = m15_data[inst].loc[ts, "close"]
            price_ret = (current_price - p["entry_price"]) / p["entry_price"]
            combined_floating_pnl += price_ret * p["notional_usd"]

        floating_loss_pct = -combined_floating_pnl / account_balance if account_balance > 0 else 0

        if floating_loss_pct >= GOAT_GUARD_THRESHOLD_PCT:
            triggers.append({
                "timestamp": ts,
                "combined_floating_loss_pct": floating_loss_pct,
                "positions_involved": [p["instrument"] for p in open_now],
            })
            already_triggered_for_current_position_set = True

    return {"triggers": triggers, "trigger_count": len(triggers)}


if __name__ == "__main__":
    print("=== Synthetic data test (mechanics check, NOT a real-data test) ===\n")
    # Two fake instruments, both losing simultaneously, engineered to
    # cross the 2% threshold partway through a synthetic 1-day window.
    rng = pd.date_range("2026-01-01 00:00", "2026-01-01 06:00", freq="15min")
    fake_m15 = {
        "fake_a": pd.DataFrame({"close": np.linspace(100.0, 97.0, len(rng))}, index=rng),
        "fake_b": pd.DataFrame({"close": np.linspace(50.0, 49.0, len(rng))}, index=rng),
    }
    fake_positions = [
        {"instrument": "fake_a", "entry_price": 100.0, "notional_usd": 3000.0,
         "start_date": rng[0], "end_date": rng[-1]},
        {"instrument": "fake_b", "entry_price": 50.0, "notional_usd": 2000.0,
         "start_date": rng[0], "end_date": rng[-1]},
    ]
    # fake_a: (97-100)/100 * 3000 = -90; fake_b: (49-50)/50 * 2000 = -40
    # combined = -130, against a $5000 balance = 2.6% -> should trigger
    result = check_positions_against_m15(fake_positions, account_balance=5000.0, m15_data=fake_m15)
    print(f"Triggers found: {result['trigger_count']}")
    for t in result["triggers"]:
        print(f"  {t['timestamp']}: {t['combined_floating_loss_pct']:.2%} loss, "
              f"positions: {t['positions_involved']}")
    expected = 1
    status = "PASS" if result["trigger_count"] == expected else "FAIL"
    print(f"\nExpected {expected} trigger (combined loss crosses 2% partway through the "
          f"synthetic window) -> {status}")
    print("\nThis confirms the MECHANICS work on synthetic data. Run this file's "
          "logic against your real data/raw/*_M15.csv files before trusting it "
          "for the actual Goat Guard reconstruction — not yet done.")