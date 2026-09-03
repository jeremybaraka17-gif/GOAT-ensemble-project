"""
ensemble_ev.py — the actual P31/P32 ensemble-EV objective: sweeps the
confirmed risk grid across many REAL calendar-anchored starting points
(not synthetic bootstrap — see decisions_log.md, "quant call: real
calendar-anchored sweep, not statistical bootstrap," for why block-
bootstrap is incompatible with exact M15 Goat Guard reconstruction).

For each (risk_level, start_date) pair, runs the full rebuy chain
(p32_multistage_sim.run_lifecycle_chain) over a fixed wall-clock budget,
computes real dollar payout value per cycle, and reports net EV per
dollar of account cost — the actual quantity P31/P32 exists to estimate.

HONEST LIMITATIONS, carried forward from everything this file depends on
— not repeated in full here, see decisions_log.md and each underlying
module's own docstring:
  - Cost model in p32_trend_engine.py is simplified (flat spread, not
    vol-scaled).
  - Sizing is constant-risk, not cushion-proportional (a deliberate,
    reasoned choice — see decisions_log.md).
  - Payout dollar amounts ARE now deducted from simulated equity at the
    point of withdrawal (fixed 2026-09-02, see decisions_log.md — this
    was previously an open gap; results from before that date do not
    reflect this fix and should be treated as superseded, not
    equivalent).
  - Payout cap for payout #3+ is an unconfirmed, conservative assumption.
  - Real starting-point sweep instead of statistical bootstrap means
    fewer, but genuine, paths (~100+, not thousands) — and paths starting
    within wall_clock_budget_days of the data's end will be truncated by
    DATA_EXHAUSTED rather than a true budget-complete result. Reported
    separately, not blended silently into the headline numbers.

Usage:
    python backtest\\ensemble_ev.py
"""
import os
import sys
import time
import multiprocessing as mp
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p32_signal_prep import load_and_prep_all, INSTRUMENTS
from p32_multistage_sim import (
    run_lifecycle_chain, ACCOUNT_COST_USD, PAYOUT_SPLIT_PCT,
)

# Risk grid — FOCUSED RESWEEP, 1.00%-1.80%, on the FIRST wide sweep run
# against a fully-corrected engine (real vol-scaled costs AND correct
# POINT_SIZE, both confirmed 2026-09-02). This wide sweep showed a
# smooth, monotonic climb from 0.50% through 1.30% (Incl.trunc EV/day
# 0.161 -> 1.109, the strongest CLEAN — under 11% truncated, not flagged
# — number on the whole grid), then a genuine anomaly: 1.60% showed a
# graduation-rate DIP (78.7%, sandwiched between 1.30%'s 92.7% and
# 2.00%'s 100%) and the largest sensitivity-check gap on the table
# (-0.726). Not confidently explained yet — real structure specific to
# that level, or a sampling artifact from only 122 correlated 21-day-
# spaced points, the same open question this method has already
# resolved twice before for two earlier "peaks" that turned out to be
# partly or wholly artifacts.
#
# This resweep covers BOTH open questions in one grid: sharpening where
# the true peak sits between 1.00% and 1.30%, AND resolving whether
# 1.60%'s dip is real. Wider than prior focused resweeps (17 levels, not
# 13) since the region of interest itself is wider this time — same
# 0.05% step convention, same 10-day spacing.
RISK_GRID = [0.0100, 0.0105, 0.0110, 0.0115, 0.0120, 0.0125, 0.0130,
             0.0135, 0.0140, 0.0145, 0.0150, 0.0155, 0.0160, 0.0165,
             0.0170, 0.0175, 0.0180]

# Wall-clock budget per simulated path — unchanged, matches P31's
# example convention and every prior sweep, for direct comparability.
WALL_CLOCK_BUDGET_DAYS = 365 * 3

# Starting-point spacing — tightened to 10 days for this focused
# resweep, same convention and correlation caveat as every prior
# focused resweep.
START_SPACING_DAYS = 10


def get_candidate_start_dates(prepared, instruments=None):
    """Real calendar dates, spaced START_SPACING_DAYS apart, across the
    full common-coverage history. Returns (start_dates, common_start,
    common_end) so the caller can see exactly what range was used."""
    instruments = instruments or INSTRUMENTS
    valid_frames = {i: prepared[i].dropna(subset=["atr20", "signal"]) for i in instruments}
    common_start = max(df.index.min() for df in valid_frames.values())
    common_end = min(df.index.max() for df in valid_frames.values())
    dates = []
    d = common_start
    while d <= common_end:
        dates.append(d)
        d = d + pd.Timedelta(days=START_SPACING_DAYS)
    return dates, common_start, common_end


def evaluate_chain_result(chain_result, start_date, wall_clock_budget_days):
    """Converts a run_lifecycle_chain() result into the real dollar
    figures the ensemble-EV objective needs: total payout value (read
    directly from each payout event's stored payout_amount, computed
    once at the point of withdrawal in run_full_lifecycle — not
    recomputed here, so the two can never drift apart), net EV, the
    diagnostics that turned out to matter after the first sweep — days/
    attempts to graduate, payout cap-binding rate — AND the time-adjusted
    metric that turned out to matter after the SECOND sweep: EV/$ alone
    treats a path graduating in 315 days identically to one taking 886
    days, as long as the dollar total matches. That's not economically
    right — capital tied up 3x longer for the same return is a worse
    outcome, and the second sweep showed days-to-graduate varying by
    nearly 3x across the risk grid while EV/$ barely moved. ev_per_day
    fixes that.

    total_elapsed_days: for a GRADUATED path, the actual calendar time
    taken (rewards fast graduation, as it should). For a path that
    exhausted its full wall-clock budget WITHOUT graduating, uses the
    full budget as the time base — economically correct (capital was
    tied up the entire budget period regardless of partial progress),
    not just an approximation of convenience."""
    total_payout_dollars = 0.0
    n_payouts_total = 0
    n_payouts_cap_bound = 0
    for attempt in chain_result["attempts"]:
        for p in attempt["payouts"]:
            # payout_amount is now computed ONCE, at the point of
            # withdrawal, inside p32_multistage_sim.run_full_lifecycle
            # (needed there to correctly deduct it from carried-forward
            # equity — see that function's docstring). Reading the stored
            # value here rather than recomputing it, so the two can never
            # silently drift apart.
            payout_amount = p["payout_amount"]
            total_payout_dollars += payout_amount
            n_payouts_total += 1
            uncapped_amount = max(0.0, p["cycle_profit"] * PAYOUT_SPLIT_PCT)
            if payout_amount < uncapped_amount - 0.01:  # small tolerance for float comparison
                n_payouts_cap_bound += 1

    net_ev = total_payout_dollars - chain_result["total_cost"]
    net_ev_per_dollar = net_ev / chain_result["total_cost"] if chain_result["total_cost"] > 0 else 0.0

    days_to_graduate = None
    if chain_result["graduated"] and chain_result["attempts"]:
        final_end_date = chain_result["attempts"][-1]["end_date"]
        days_to_graduate = (final_end_date - start_date).days

    # BUGFIX (2026-09-02): previously used wall_clock_budget_days as the
    # time base for EVERY non-graduated path, including DATA_EXHAUSTED
    # ones — wrong for that case specifically. A path cut short because
    # real market data ran out never actually consumed the full nominal
    # budget; charging it the full budget as a time base UNDERSTATES its
    # true ev_per_day. Worse, DATA_EXHAUSTED paths were being silently
    # EXCLUDED from headline stats entirely (see summarize()) — meaning
    # this bug never even showed up, but the exclusion itself raised a
    # separate, real concern: if truncation disproportionately removes
    # slower/more-rebuy-heavy (i.e. worse-performing) paths at some risk
    # levels more than others, the reported means become survivorship-
    # biased in a way that's correlated with risk level. Fixed on BOTH
    # fronts: real elapsed time computed correctly for DATA_EXHAUSTED
    # paths, AND they're no longer silently dropped — summarize() now
    # reports both an excluding-truncated and an including-truncated
    # version side by side, so this sensitivity is visible, not hidden.
    if chain_result["graduated"]:
        total_elapsed_days = days_to_graduate
    elif chain_result["stopped_reason"] == "DATA_EXHAUSTED":
        if chain_result["attempts"]:
            actual_end = chain_result["attempts"][-1]["end_date"]
            total_elapsed_days = max(1, (actual_end - start_date).days)
        else:
            total_elapsed_days = wall_clock_budget_days  # fallback, shouldn't happen
    else:
        # BUDGET_EXHAUSTED: capital genuinely tied up for the full
        # nominal budget without graduating — that IS the economically
        # correct time base here, unlike the DATA_EXHAUSTED case above.
        total_elapsed_days = wall_clock_budget_days

    ev_per_day = net_ev / total_elapsed_days if total_elapsed_days and total_elapsed_days > 0 else 0.0

    return {
        "total_payout_dollars": total_payout_dollars,
        "total_cost": chain_result["total_cost"],
        "net_ev": net_ev,
        "net_ev_per_dollar": net_ev_per_dollar,
        "ev_per_day": ev_per_day,
        "total_elapsed_days": total_elapsed_days,
        "graduated": chain_result["graduated"],
        "stopped_reason": chain_result["stopped_reason"],
        "n_attempts": chain_result["n_attempts"],
        "days_to_graduate": days_to_graduate,
        "n_payouts_total": n_payouts_total,
        "n_payouts_cap_bound": n_payouts_cap_bound,
    }


def _run_one_risk_level(args):
    """
    Runs ALL starting points for ONE risk level. Pulled out as a
    standalone, top-level, picklable function specifically so it can be
    handed to multiprocessing.Pool — Windows' multiprocessing uses the
    'spawn' method, which requires worker functions to be importable at
    module level (a nested/local function, or a lambda, CANNOT be
    pickled and would fail silently or with a confusing error).

    args is a single tuple (not multiple positional args) because
    Pool.map() only passes one argument per call — starmap() is an
    alternative but map() with a packed tuple is simpler to reason about
    here.

    Returns (risk, risk_results) so the caller can reassemble the
    {risk: results} dict after all workers finish, since dict ordering
    isn't guaranteed to come back in submission order under Pool.map()
    even though in practice it usually does.
    """
    risk, prepared, start_dates, instruments = args
    t0 = time.time()
    risk_results = []
    # Shared caches across every starting point at THIS risk level —
    # the full historical return series only depends on risk_per_trade,
    # not on start_date, so this avoids recomputing it per start date.
    # Each worker PROCESS gets its own cache (no cross-process sharing —
    # that would need a multiprocessing.Manager and the added complexity
    # isn't worth it here, since the real win is parallelizing ACROSS
    # risk levels, not sharing cache within one).
    sim_cache = {}
    m15_cache = {}
    for start_date in start_dates:
        try:
            chain_result = run_lifecycle_chain(
                prepared, risk_per_trade=risk, start_date=start_date,
                wall_clock_budget_days=WALL_CLOCK_BUDGET_DAYS,
                instruments=instruments, sim_cache=sim_cache, m15_cache=m15_cache,
            )
            evaluated = evaluate_chain_result(chain_result, start_date, WALL_CLOCK_BUDGET_DAYS)
            evaluated["start_date"] = start_date
            evaluated["error"] = None
        except Exception as e:
            evaluated = {"start_date": start_date, "error": str(e),
                         "net_ev_per_dollar": None, "graduated": None,
                         "stopped_reason": "ERROR", "n_attempts": None,
                         "days_to_graduate": None, "n_payouts_total": 0,
                         "n_payouts_cap_bound": 0, "ev_per_day": None,
                         "total_elapsed_days": None}
        risk_results.append(evaluated)

    elapsed = time.time() - t0
    n_errors = sum(1 for r in risk_results if r["error"] is not None)
    print(f"  Risk {risk:.2%} done: {len(risk_results)} starting points, "
          f"{n_errors} errors, {elapsed:.0f}s")
    return risk, risk_results


def run_sweep(prepared, instruments=None, n_workers=None):
    """
    Runs the full sweep: every risk level x every candidate starting
    date. Returns {risk_level: [per-start-date result dicts]}.

    PARALLELIZED ACROSS RISK LEVELS. Each risk level's full evaluation
    (all starting points) is completely independent of every other risk
    level's — nothing about testing 1.1% depends on 1.0% having already
    run. That makes this a natural fit for multiprocessing: instead of
    running RISK_GRID's levels one after another on a single core, they
    run SIMULTANEOUSLY across however many CPU cores are available.

    n_workers: number of parallel processes. Defaults to os.cpu_count()
    minus 1 (leave one core free for the OS/other work) — override if
    you want to use all cores, or fewer (e.g. to keep the machine usable
    for other things while this runs).

    TRADEOFF, stated plainly: fine-grained "10/122 done" progress within
    a risk level is NOT available in parallel mode — each worker prints
    once, when its ENTIRE risk level finishes, not incrementally. This
    is a real loss of visibility into whether a specific risk level is
    still progressing or has stalled, traded for the much larger
    wall-clock time win. If something looks stuck with no output for a
    long time, that's harder to diagnose than in the old serial version.

    Per-path error handling is UNCHANGED — one failed (risk, start_date)
    combination still doesn't kill anything else, exactly as before.
    """
    instruments = instruments or INSTRUMENTS
    start_dates, common_start, common_end = get_candidate_start_dates(prepared, instruments)
    print(f"Candidate starting points: {len(start_dates)}, spaced {START_SPACING_DAYS} days apart, "
          f"{common_start.date()} to {common_end.date()}\n")

    if n_workers is None:
        n_workers = max(1, (os.cpu_count() or 2) - 1)
    n_workers = min(n_workers, len(RISK_GRID))  # no point spawning more workers than risk levels
    print(f"Running {len(RISK_GRID)} risk levels across {n_workers} parallel worker processes "
          f"(machine reports {os.cpu_count()} CPU cores total).\n")

    work_items = [(risk, prepared, start_dates, instruments) for risk in RISK_GRID]

    results = {}
    with mp.Pool(processes=n_workers) as pool:
        for risk, risk_results in pool.imap_unordered(_run_one_risk_level, work_items):
            results[risk] = risk_results

    # Reassemble in RISK_GRID's original order for consistent, predictable
    # table output regardless of which worker finished first.
    results = {risk: results[risk] for risk in RISK_GRID}
    return results, start_dates, common_end


def summarize(results, common_end):
    """
    Prints the actual ensemble-EV summary per risk level. Two tables now
    for EV/day specifically: EXCLUDING truncated (DATA_EXHAUSTED) paths
    (the original approach) and INCLUDING them, scored on their real
    partial outcome (see evaluate_chain_result's 2026-09-02 bugfix note)
    rather than dropped. Shown side by side because excluding them is a
    real methodological risk, not a neutral simplification: if
    truncation disproportionately removes slower/more-rebuy-heavy paths
    at some risk levels more than others (plausible — more rebuys means
    more real calendar time consumed, means more likely to run into the
    data-exhaustion wall), the excluding-truncated mean becomes
    survivorship-biased in a way correlated with risk level itself.
    Reporting both makes that sensitivity visible instead of hidden
    behind a single number.
    """
    print("\n" + "=" * 70)
    print("ENSEMBLE-EV SUMMARY (net payout $ per $1 of cost, AND per day)")
    print("=" * 70)
    header = f"{'Risk':>7s} {'N (ok)':>7s} {'N (err)':>8s} {'N (full)':>9s} {'N (trunc)':>10s} " \
             f"{'Mean EV/$':>10s} {'Std EV/$':>9s} {'Mean EV/day':>12s} {'Std EV/day':>11s} {'Grad %':>7s}"
    print(header)

    for risk in RISK_GRID:
        risk_results = results[risk]
        ok = [r for r in risk_results if r["error"] is None]
        errored = [r for r in risk_results if r["error"] is not None]
        # a path is "truncated" if it did NOT graduate AND its stopped
        # reason was data exhaustion (started too close to common_end to
        # get a full wall_clock_budget_days run)
        full_budget = [r for r in ok if r["stopped_reason"] != "DATA_EXHAUSTED"]
        truncated = [r for r in ok if r["stopped_reason"] == "DATA_EXHAUSTED"]

        if full_budget:
            ev_per_dollar_values = [r["net_ev_per_dollar"] for r in full_budget]
            ev_per_day_values = [r["ev_per_day"] for r in full_budget]
            mean_ev = np.mean(ev_per_dollar_values)
            std_ev = np.std(ev_per_dollar_values)
            mean_ev_day = np.mean(ev_per_day_values)
            std_ev_day = np.std(ev_per_day_values)
            grad_rate = np.mean([r["graduated"] for r in full_budget]) * 100
        else:
            mean_ev, std_ev, grad_rate = float("nan"), float("nan"), float("nan")
            mean_ev_day, std_ev_day = float("nan"), float("nan")

        print(f"{risk:>7.2%} {len(ok):>7d} {len(errored):>8d} {len(full_budget):>9d} "
              f"{len(truncated):>10d} {mean_ev:>10.3f} {std_ev:>9.3f} "
              f"{mean_ev_day:>12.4f} {std_ev_day:>11.4f} {grad_rate:>6.1f}%")

    print("\nMean/Std EV/$ = the ORIGINAL P31/P32 objective (net payout dollars per "
          "dollar of account cost) — treats a fast graduation and a slow one "
          "identically if the dollar total matches. Kept for continuity, not because "
          "it's the right sole metric.")
    print("Mean/Std EV/day (this table) = computed EXCLUDING truncated paths, same "
          "as every prior sweep — kept for direct comparability to those results. "
          "See the SENSITIVITY table below for the including-truncated comparison.")
    print("N (full) = paths that ran a complete wall-clock budget without hitting "
          "the end of available data.")
    print("N (trunc) = paths cut short by real data exhaustion — EXCLUDED from this "
          "table's stats, but see the sensitivity table below for what including "
          "them (scored fairly on their real partial outcome) does to the picture.")
    print("N (err) = paths that raised an exception (e.g. missing M15 data in an")
    print("environment that doesn't have it) — recorded, not silently dropped.")

    # --- Sensitivity table: including truncated paths, scored fairly ---
    print("\n" + "=" * 70)
    print("SENSITIVITY CHECK: EV/day INCLUDING truncated paths")
    print("=" * 70)
    header_sens = f"{'Risk':>7s} {'Excl.trunc':>11s} {'Incl.trunc':>11s} {'Delta':>8s} {'%truncated':>11s}"
    print(header_sens)
    for risk in RISK_GRID:
        risk_results = results[risk]
        ok = [r for r in risk_results if r["error"] is None]
        full_budget = [r for r in ok if r["stopped_reason"] != "DATA_EXHAUSTED"]
        all_non_error = ok  # full_budget + truncated together

        excl_mean = np.mean([r["ev_per_day"] for r in full_budget]) if full_budget else float("nan")
        incl_mean = np.mean([r["ev_per_day"] for r in all_non_error]) if all_non_error else float("nan")
        delta = incl_mean - excl_mean if not (np.isnan(excl_mean) or np.isnan(incl_mean)) else float("nan")
        pct_trunc = (len(ok) - len(full_budget)) / len(ok) * 100 if ok else float("nan")

        flag = "  <-- large gap" if not np.isnan(delta) and abs(delta) > 0.3 else ""
        print(f"{risk:>7.2%} {excl_mean:>11.4f} {incl_mean:>11.4f} {delta:>+8.4f} "
              f"{pct_trunc:>10.1f}%{flag}")

    print("\nExcl.trunc = same 'Mean EV/day' as the main table above (truncated paths "
          "dropped). Incl.trunc = same metric but truncated paths INCLUDED, scored on "
          "their real elapsed time and real partial EV (not the full nominal budget — "
          "see evaluate_chain_result's 2026-09-02 bugfix). Delta = how much the "
          "headline number moves once truncated paths are counted fairly instead of "
          "dropped. A large delta at a risk level with a high %truncated is the "
          "specific signature of survivorship bias in the excl.trunc number — treat "
          "any risk level flagged '<-- large gap' with real caution, not as settled.")

    # --- Second table: the diagnostics that turned out to matter after
    # the first sweep showed EV/$ was flat across the whole risk grid.
    # This is where speed-to-graduate and cap-binding actually
    # differentiate risk levels, if anything does. ---
    print("\n" + "=" * 70)
    print("SPEED & CAP-BINDING DIAGNOSTICS (full-budget paths only)")
    print("=" * 70)
    header2 = f"{'Risk':>7s} {'N Grad':>7s} {'Mean Attempts':>14s} {'Std Attempts':>13s} " \
              f"{'Mean Days':>10s} {'Std Days':>9s} {'Cap-Bound %':>12s}"
    print(header2)

    for risk in RISK_GRID:
        risk_results = results[risk]
        ok = [r for r in risk_results if r["error"] is None]
        full_budget = [r for r in ok if r["stopped_reason"] != "DATA_EXHAUSTED"]
        graduated_full = [r for r in full_budget if r["graduated"]]

        if graduated_full:
            attempts_vals = [r["n_attempts"] for r in graduated_full]
            days_vals = [r["days_to_graduate"] for r in graduated_full if r["days_to_graduate"] is not None]
            mean_attempts, std_attempts = np.mean(attempts_vals), np.std(attempts_vals)
            mean_days = np.mean(days_vals) if days_vals else float("nan")
            std_days = np.std(days_vals) if days_vals else float("nan")
        else:
            mean_attempts = std_attempts = mean_days = std_days = float("nan")

        total_payouts = sum(r["n_payouts_total"] for r in full_budget)
        total_cap_bound = sum(r["n_payouts_cap_bound"] for r in full_budget)
        cap_bound_pct = (total_cap_bound / total_payouts * 100) if total_payouts > 0 else float("nan")

        print(f"{risk:>7.2%} {len(graduated_full):>7d} {mean_attempts:>14.2f} {std_attempts:>13.2f} "
              f"{mean_days:>10.1f} {std_days:>9.1f} {cap_bound_pct:>11.1f}%")

    print("\nMean Attempts / Mean Days = average rebuys and calendar time needed to "
          "reach graduation, among paths that actually graduated within their full "
          "budget. Lower is better on BOTH — fewer costly rebuys, faster to funded.")
    print("Cap-Bound % = of all payouts across full-budget paths, what fraction hit "
          "the $200 (4% of $5,000) cap rather than being limited by actual edge. A "
          "high percentage here is exactly the mechanism that would explain flat "
          "EV/$ across the risk grid: if the cap binds most of the time, higher risk "
          "can only reach the same ceiling faster, not exceed it.")


if __name__ == "__main__":
    print("Loading and prepping signal data...\n")
    prepared = load_and_prep_all(instruments=INSTRUMENTS)

    print("\nStarting full ensemble sweep, PARALLELIZED across CPU cores — each "
          "risk level runs as its own worker process, all simultaneously. "
          "Progress prints once per risk level, when that level's entire run "
          "finishes (fine-grained per-starting-point progress isn't available "
          "in parallel mode — see run_sweep()'s docstring for the tradeoff).\n")

    t_start = time.time()
    results, start_dates, common_end = run_sweep(prepared, instruments=INSTRUMENTS)
    total_elapsed = time.time() - t_start
    print(f"\nFull sweep completed in {total_elapsed:.0f}s ({total_elapsed/60:.1f} min)")

    summarize(results, common_end)

    print("\n\nThis is a FOCUSED RESWEEP of the 1.00%-1.80% band — the first wide "
          "sweep against the fully-corrected engine (real costs AND correct "
          "POINT_SIZE) showed a smooth climb from 0.50% to 1.30% (the strongest "
          "CLEAN number on that grid), then a genuine graduation-rate dip at 1.60% "
          "(78.7%, sandwiched between cleaner neighbors) not yet confirmed as real "
          "structure vs. sampling noise. This resweep answers both questions at "
          "once: where the true peak sits between 1.00%-1.30%, and whether 1.60%'s "
          "dip survives finer resolution — same method that already resolved two "
          "earlier apparent peaks this project found.")
    print("\nRead the SENSITIVITY CHECK table below FIRST, not the headline table "
          "— same discipline as every run since the truncation bug was found.")
    print("\nSTATISTICAL CAVEAT: starting points 10 days apart are NOT independent "
          "draws — real but limited additional power versus 21-day spacing, not "
          "power proportional to the point-count increase.")
    print("\nRemaining honest limitations: sizing is constant-risk by default "
          "(cushion-proportional sizing is fully wired and safe to use via "
          "sizing_mode='cushion' on the underlying functions, but NOT yet exposed "
          "as an option in this sweep script), and the payout-3+ cap is a "
          "deliberately-closed conservative assumption. The cost model and "
          "POINT_SIZE are both now genuinely confirmed correct (2026-09-02, "
          "verified against the live account and matching on both development "
          "machines).")