"""
compare_sizing_modes.py — REVISED (2026-09-02, second pass). First
version tested cushion sizing at the SAME 1.4% base risk locked for
constant-risk sizing, and found a total, one-sided failure: 0/52
full-budget completions for cushion vs. 45/52 for constant. That result
was correct but the comparison itself was flawed: 1.4% was tuned to be
optimal FOR constant-risk sizing specifically. Cushion sizing trades at
LESS than its nominal base risk whenever there's any drawdown from its
own high-water-mark — its effective average risk sits below the nominal
figure almost by construction, so testing it at a risk level optimized
for a different mechanism was never a fair test of cushion sizing
itself, just a demonstration that a throttled mechanism struggles to hit
a target tuned for an unthrottled one.

THIS VERSION tests cushion sizing at HIGHER candidate base risks
(2.0%, 3.0% — bracketing a plausible "fair" cushion-mode risk level,
chosen before running, not tuned to produce a particular result) against
the ALREADY-ESTABLISHED constant-1.4% reference numbers, rather than
re-running constant mode again (no need — those numbers are already
known and don't change).

Same honest scope caveat as before: 52 points per candidate is a
reduced, first-pass comparison, not a fully statistically-matched sweep.

Usage:
    python backtest\\compare_sizing_modes.py
"""
import os
import sys
import time
import multiprocessing as mp
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p32_signal_prep import load_and_prep_all, INSTRUMENTS
from p32_multistage_sim import run_lifecycle_chain, ACCOUNT_COST_USD
from ensemble_ev import evaluate_chain_result, get_candidate_start_dates

# Already established, first pass (2026-09-02) — NOT re-run here.
CONSTANT_REFERENCE = {
    "risk": 0.014, "n": 52, "n_full": 45, "n_trunc": 7,
    "excl_trunc": 1.4851, "incl_trunc": 1.2089, "grad_rate": 95.6,
}

# Cushion base-risk candidates for this second pass — deliberately
# higher than 1.4%, bracketing a plausible "fair" cushion-mode risk
# level given its self-throttling reduces EFFECTIVE average risk below
# the nominal figure. 1.4% itself already tested (0/52 full-budget
# completions, InclTrunc -0.137) — not re-run here, but shown in the
# results table for continuity.
CUSHION_RISK_CANDIDATES = [0.014, 0.020, 0.030]
ALREADY_TESTED = {0.014: {"n": 52, "n_full": 0, "n_trunc": 52,
                            "excl_trunc": float("nan"), "incl_trunc": -0.1370,
                            "grad_rate": float("nan")}}

WALL_CLOCK_BUDGET_DAYS = 365 * 3
COMPARISON_SPACING_DAYS = 42  # unchanged from the first pass


def _run_one_comparison_point(args):
    """One (risk_level, start_date) work item, always sizing_mode='cushion'
    in this revised version — constant mode's reference numbers are
    already established and don't need re-running."""
    prepared, risk_level, start_date = args
    try:
        chain_result = run_lifecycle_chain(
            prepared, risk_per_trade=risk_level, start_date=start_date,
            wall_clock_budget_days=WALL_CLOCK_BUDGET_DAYS,
            instruments=INSTRUMENTS, sizing_mode="cushion",
        )
        evaluated = evaluate_chain_result(chain_result, start_date, WALL_CLOCK_BUDGET_DAYS)
        evaluated["error"] = None
    except Exception as e:
        evaluated = {"error": str(e), "graduated": None, "stopped_reason": "ERROR",
                     "ev_per_day": None, "n_attempts": None}
    evaluated["start_date"] = start_date
    evaluated["risk_level"] = risk_level
    return evaluated


def run_comparison(prepared, n_workers=None):
    start_dates_full, common_start, common_end = get_candidate_start_dates(prepared, INSTRUMENTS)
    start_dates = []
    last_taken = None
    for d in start_dates_full:
        if last_taken is None or (d - last_taken).days >= COMPARISON_SPACING_DAYS:
            start_dates.append(d)
            last_taken = d

    risk_levels_to_run = [r for r in CUSHION_RISK_CANDIDATES if r not in ALREADY_TESTED]
    print(f"Comparison starting points: {len(start_dates)}, spaced ~{COMPARISON_SPACING_DAYS} "
          f"days apart, {common_start.date()} to {common_end.date()}")
    print(f"Cushion risk candidates to run this pass: {[f'{r:.1%}' for r in risk_levels_to_run]} "
          f"(1.4% already tested in the first pass, shown for reference)\n")

    if n_workers is None:
        n_workers = max(1, (os.cpu_count() or 2) - 1)
    work_items = [(prepared, risk, d) for risk in risk_levels_to_run for d in start_dates]
    print(f"Total work items: {len(work_items)} ({len(start_dates)} points x "
          f"{len(risk_levels_to_run)} risk levels), {n_workers} parallel workers\n")

    results = {risk: [] for risk in risk_levels_to_run}
    t_start = time.time()
    with mp.Pool(processes=n_workers) as pool:
        for i, evaluated in enumerate(pool.imap_unordered(_run_one_comparison_point, work_items)):
            results[evaluated["risk_level"]].append(evaluated)
            if (i + 1) % 10 == 0:
                print(f"  {i+1}/{len(work_items)} done ({time.time()-t_start:.0f}s elapsed)")
    total_elapsed = time.time() - t_start
    print(f"\nComparison completed in {total_elapsed:.0f}s ({total_elapsed/60:.1f} min)")
    return results


def summarize_comparison(results):
    print("\n" + "=" * 78)
    print("CUSHION SIZING ACROSS CANDIDATE BASE RISKS, vs. CONSTANT-1.4% REFERENCE")
    print("=" * 78)
    header = f"{'Mode/Risk':>14s} {'N':>4s} {'N full':>7s} {'N trunc':>8s} " \
             f"{'ExclTrunc':>10s} {'InclTrunc':>10s} {'Grad %':>7s}"
    print(header)

    ref = CONSTANT_REFERENCE
    print(f"{'constant 1.4%':>14s} {ref['n']:>4d} {ref['n_full']:>7d} {ref['n_trunc']:>8d} "
          f"{ref['excl_trunc']:>10.4f} {ref['incl_trunc']:>10.4f} {ref['grad_rate']:>6.1f}%"
          f"  <-- REFERENCE (already established)")

    for risk, prior in ALREADY_TESTED.items():
        excl = prior["excl_trunc"]
        incl = prior["incl_trunc"]
        grad = prior["grad_rate"]
        excl_str = f"{excl:.4f}" if not np.isnan(excl) else "nan"
        grad_str = f"{grad:.1f}%" if not np.isnan(grad) else "nan%"
        print(f"{'cushion ' + f'{risk:.1%}':>14s} {prior['n']:>4d} {prior['n_full']:>7d} "
              f"{prior['n_trunc']:>8d} {excl_str:>10s} {incl:>10.4f} {grad_str:>7s}"
              f"  <-- already tested, first pass")

    for risk in CUSHION_RISK_CANDIDATES:
        if risk not in results:
            continue
        risk_results = results[risk]
        ok = [r for r in risk_results if r["error"] is None]
        errored = [r for r in risk_results if r["error"] is not None]
        full_budget = [r for r in ok if r["stopped_reason"] != "DATA_EXHAUSTED"]
        truncated = [r for r in ok if r["stopped_reason"] == "DATA_EXHAUSTED"]

        excl_mean = np.mean([r["ev_per_day"] for r in full_budget]) if full_budget else float("nan")
        incl_mean = np.mean([r["ev_per_day"] for r in ok]) if ok else float("nan")
        grad_rate = np.mean([r["graduated"] for r in full_budget]) * 100 if full_budget else float("nan")

        print(f"{'cushion ' + f'{risk:.1%}':>14s} {len(ok):>4d} {len(full_budget):>7d} "
              f"{len(truncated):>8d} {excl_mean:>10.4f} {incl_mean:>10.4f} {grad_rate:>6.1f}%")
        if errored:
            print(f"  ({len(errored)} errored at this risk level — see per-path 'error' field)")

    print("\nExclTrunc/InclTrunc = EV/day excluding vs including truncated (DATA_EXHAUSTED) "
          "paths. InclTrunc is the more trustworthy number, same reasoning as every recent "
          "sweep.")
    print("\nThis answers the real question the first pass couldn't: whether cushion sizing "
          "can be competitive with constant-risk sizing AT A FAIR (higher) BASE RISK, not "
          "whether it's competitive at a risk level tuned for a different mechanism.")
    print(f"\nSample size: 52 points per risk level. Reduced-scope comparison — a close call "
          f"between any cushion candidate and the constant-1.4% reference should not be "
          f"treated as settled without more points; a large, consistent gap is trustworthy "
          f"at this scope.")


if __name__ == "__main__":
    print("Loading and prepping signal data...\n")
    prepared = load_and_prep_all(instruments=INSTRUMENTS)
    print()

    results = run_comparison(prepared)
    summarize_comparison(results)