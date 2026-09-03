"""
generate_montecarlo_data.py — runs the LOCKED configuration (1.4% base
risk, constant sizing) across the full candidate starting-point set and
saves EVERY individual path's result to a CSV — not just aggregate
statistics. Every prior sweep in this project only captured summary
stats (mean/std/N) in terminal output; this is the first script that
preserves per-path results, needed for genuine Monte Carlo distribution
figures (histograms, not just bar charts of means).

Usage:
    python backtest\\generate_montecarlo_data.py

Output: montecarlo_1.4pct_locked.csv in the same folder, one row per
starting point, with columns: start_date, net_ev, ev_per_day,
total_elapsed_days, graduated, stopped_reason, n_attempts,
n_payouts_total, n_payouts_cap_bound.

After running, upload the CSV back into the chat and real distribution
figures (histogram of EV/day, days-to-graduate, etc.) can be built from
genuine data — not a fabricated shape guessed from the mean and std
already on record.
"""
import os
import sys
import time
import csv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p32_signal_prep import load_and_prep_all, INSTRUMENTS
from p32_multistage_sim import run_lifecycle_chain
from ensemble_ev import evaluate_chain_result, get_candidate_start_dates, WALL_CLOCK_BUDGET_DAYS

LOCKED_RISK = 0.014
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "montecarlo_1.4pct_locked.csv")


def main():
    print("Loading and prepping signal data...\n")
    prepared = load_and_prep_all(instruments=INSTRUMENTS)

    start_dates, common_start, common_end = get_candidate_start_dates(prepared, INSTRUMENTS)
    print(f"\nRunning locked config (risk={LOCKED_RISK:.1%}, constant sizing) across "
          f"{len(start_dates)} starting points, {common_start.date()} to {common_end.date()}\n")

    sim_cache = {}
    m15_cache = {}
    rows = []
    t0 = time.time()

    for i, start_date in enumerate(start_dates):
        try:
            chain_result = run_lifecycle_chain(
                prepared, risk_per_trade=LOCKED_RISK, start_date=start_date,
                wall_clock_budget_days=WALL_CLOCK_BUDGET_DAYS,
                instruments=INSTRUMENTS, sim_cache=sim_cache, m15_cache=m15_cache,
            )
            evaluated = evaluate_chain_result(chain_result, start_date, WALL_CLOCK_BUDGET_DAYS)
            error = None
        except Exception as e:
            evaluated = {"net_ev": None, "ev_per_day": None, "total_elapsed_days": None,
                         "graduated": None, "stopped_reason": "ERROR", "n_attempts": None,
                         "n_payouts_total": None, "n_payouts_cap_bound": None}
            error = str(e)

        rows.append({
            "start_date": start_date.date().isoformat(),
            "net_ev": evaluated.get("net_ev"),
            "ev_per_day": evaluated.get("ev_per_day"),
            "total_elapsed_days": evaluated.get("total_elapsed_days"),
            "graduated": evaluated.get("graduated"),
            "stopped_reason": evaluated.get("stopped_reason"),
            "n_attempts": evaluated.get("n_attempts"),
            "n_payouts_total": evaluated.get("n_payouts_total"),
            "n_payouts_cap_bound": evaluated.get("n_payouts_cap_bound"),
            "error": error,
        })

        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(start_dates)} done ({time.time()-t0:.0f}s elapsed)")

    with open(OUTPUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    total_elapsed = time.time() - t0
    print(f"\nDone in {total_elapsed:.0f}s ({total_elapsed/60:.1f} min). "
          f"Saved {len(rows)} per-path results to:\n  {OUTPUT_PATH}")
    print("\nUpload this CSV back into the chat — real Monte Carlo distribution "
          "figures (histogram of EV/day, days-to-graduate, etc.) can be built "
          "directly from it.")

    # Quick sanity check against the already-known aggregate numbers
    ok_rows = [r for r in rows if r["error"] is None]
    full_rows = [r for r in ok_rows if r["stopped_reason"] != "DATA_EXHAUSTED"]
    print(f"\nQuick sanity check: {len(ok_rows)} ok, {len(full_rows)} full-budget "
          f"(compare against the already-known 1.40% row from the risk-level sweep — "
          f"should be roughly consistent, not necessarily identical since this uses "
          f"the full candidate set at whatever spacing get_candidate_start_dates "
          f"currently uses).")


if __name__ == "__main__":
    main()