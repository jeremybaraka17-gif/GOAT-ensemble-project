"""
p32_multistage_sim.py — Step 1 -> Step 2 -> Funded state machine, layered
on top of p32_trend_engine.py's raw daily return generator. This is the
piece the trend-engine smoke test showed is necessary, not optional: at
0.8% risk uncapped, the raw engine alone produced an -11.03% max
drawdown, which BREACHES GOAT's confirmed 10% static floor. This file
enforces that floor (and the 4% daily floor, and profit targets, and
payout eligibility) day by day.

SCOPE: simulates ONE account lifecycle from a given historical start
date through Step 1 -> Step 2 -> Funded (payout cycles, graduation
trigger), stopping at the first breach, data exhaustion, or graduation.
Chaining multiple lifecycle attempts with account-cost accounting (the
actual rebuy loop / ensemble-EV objective) is ensemble_ev.py's job, built
next — this file is the single-attempt building block it will call
repeatedly across bootstrapped starting points.

GOAT GUARD IS NOW WIRED IN — real M15 reconstruction, not a placeholder.
Each 14-day funded cycle's actual trade log (from p32_trend_engine.py's
log_trades=True output, including still-open positions at cycle end) is
checked against real M15 data via goat_guard_m15.check_positions_against_m15().
Strikes are tracked CUMULATIVELY across the whole funded stage (not reset
per cycle), matching GFT's real soft-then-hard rule: 1st cumulative
trigger is a soft strike (tracked, though this engine doesn't yet model
the resulting 50%-split financial consequence in payout accounting —
that's ensemble_ev.py's job when it computes actual $ EV), 2nd cumulative
trigger is BREACH_GOAT_GUARD, a hard breach ending the lifecycle.

TWO HONEST LIMITATIONS IN THIS WIRING, NOT SILENTLY GLOSSED OVER:
1. Goat Guard's account_balance threshold is checked against a FIXED
   value (the cycle's STARTING equity), not the real day-by-day evolving
   balance GFT actually checks against. Over- or under-states the
   percentage threshold depending on whether equity grew or shrank
   during the cycle. Flagged in _check_goat_guard_for_segment()'s own
   docstring too.
2. NOT TESTED AGAINST REAL M15 DATA. goat_guard_m15.py's core function
   was verified against synthetic data only (no M15 CSVs available in
   the environment this was built in). The wiring here has not been run
   end-to-end against your actual data/raw/*_M15.csv files — that's the
   real test still needed, not yet done.

INTERPRETATION FLAGGED, NOT CONFIRMED: the FAQ states funded-stage
payout eligibility requires N valid trading days (4, since this account
was purchased after 2026-07-25) with payouts on a bi-weekly (14-day)
cycle, and that the day-count "resets after each payout." It does NOT
specify precisely what happens if a 14-day cycle elapses WITHOUT hitting
N valid days. This implementation assumes: evaluate eligibility at the
end of each fixed 14-day window; if met, log a payout and start a fresh
window; if not met, no payout that cycle, still start a fresh window
(no breach). This is a reasonable reading, not an independently
confirmed one — if payout timing precision matters later, confirm
against GFT support/docs directly rather than trusting this assumption.

VALID-DAY THRESHOLD, CONFIRMED WORDING: "profit made on that day is at
least 0.5% of the INITIAL account balance" — the FAQ's original $5,000
account balance, not the current phase segment's starting equity. Applied
that way here (fixed $25/day threshold for a $5,000 account), not
re-derived per phase.

SIMPLIFICATION, FLAGGED: position state does not carry across phase
transitions — each phase segment (Step 1, Step 2, Funded) starts flat
and re-runs the trend engine from that point forward with the new
phase's leverage. Real accounts likely carry open positions through the
Step-2-to-funded review. Net effect on results is not characterized;
flagged as a modeling simplification, not a hidden bug.
"""
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p32_trend_engine import simulate_portfolio
from p32_signal_prep import INSTRUMENTS
from goat_guard_m15 import check_positions_against_m15, load_m15

# Confirmed rules — see config/strategy.yaml / research/decisions_log.md
DAILY_DD_PCT = 0.04
STATIC_MAX_LOSS_PCT = 0.10
STEP1_TARGET = 0.08
STEP2_TARGET = 0.06
MIN_VALID_DAYS_EVAL = 3
VALID_DAY_MIN_PROFIT_PCT = 0.005          # 0.5% of the ORIGINAL account balance
FUNDED_VALID_DAYS_PER_CYCLE = 4            # this account purchased >= 2026-07-25
PAYOUT_CYCLE_DAYS = 14
GRADUATION_PAYOUT_COUNT = 3                 # locked, see decisions_log.md
ORIGINAL_ACCOUNT_BALANCE = 5000.0


def check_goat_guard(*args, **kwargs):
    """DEPRECATED PLACEHOLDER — kept only so nothing else that imports it
    breaks. Real Goat Guard checking now happens in _run_segment via
    goat_guard_m15.check_positions_against_m15(), called once per funded
    segment against that segment's actual trade log. See
    _check_goat_guard_for_segment() below."""
    return {"triggered": False, "level": None}


def _get_cached_simulation(prepared, risk_per_trade, phase, aggregate_daily_risk_cap,
                             instruments, log_trades, cache):
    """
    Caches simulate_portfolio()'s full-history result keyed by phase
    (risk_per_trade is constant across one ensemble path evaluation, so
    it doesn't need to be part of the cache key here — the caller is
    responsible for using a FRESH cache per risk_per_trade value).

    Fixes a real performance problem: _run_segment previously re-ran the
    ENTIRE historical simulation from scratch on every call, even though
    consecutive segments (Step 1 -> Step 2 -> funded cycles) only ever
    need the tail slice from their own start_date forward. Across many
    starting points and risk levels in ensemble_ev.py, that's O(n^2)-ish
    and not viable. This cache computes each (phase, log_trades) result
    ONCE and reuses it for every subsequent call within the same risk
    level's evaluation.

    APPROXIMATION, FLAGGED HONESTLY: this cache is computed at a FIXED
    reference starting_equity (ORIGINAL_ACCOUNT_BALANCE, $5,000), but
    real segments start at whatever equity the PREVIOUS segment actually
    ended at (e.g. Step 2 might start at $5,400 after Step 1 passed, not
    $5,000). Fractional daily returns are MOSTLY equity-scale-invariant
    in this engine (position sizing and P&L are both computed as
    fractions of current equity), EXCEPT for MIN_LOT rounding effects —
    a different absolute equity level can round to a different number of
    lots at the margin, producing a slightly different actual trade
    sequence than what the cached $5,000-based series represents. Given
    typical notional-per-lot sizes ($70k-$135k for the FX legs) relative
    to equity in the $4,500-$10,000+ range this project operates in,
    the expected distortion is small — but it is NOT exactly zero, and
    has not been precisely quantified. This is a deliberate
    performance-for-precision tradeoff, not a silently introduced one.
    """
    key = (phase, log_trades)
    if key not in cache:
        cache[key] = simulate_portfolio(
            prepared, risk_per_trade=risk_per_trade, phase=phase,
            starting_equity=ORIGINAL_ACCOUNT_BALANCE,
            aggregate_daily_risk_cap=aggregate_daily_risk_cap,
            instruments=instruments, log_trades=log_trades,
        )
    return cache[key]


def _check_goat_guard_for_segment(trade_log, segment_start, segment_end, account_balance,
                                    m15_cache):
    """
    Converts this segment's closed-and-still-open trade log entries into
    goat_guard_m15's expected format and checks them against real M15
    data. Returns a chronologically sorted list of trigger events within
    this segment (possibly empty).

    APPROXIMATION, flagged: account_balance is held FIXED at this
    segment's starting equity for the whole check, rather than evolving
    day-by-day as the D1 engine's equity actually changes through the
    segment. Real GFT rule checks against current balance continuously.
    Using a fixed reference will over- or under-state the percentage
    threshold depending on whether equity grew or shrank during the
    segment — a real simplification, not treated as exact.
    """
    positions_in_window = []
    for t in trade_log:
        entry_date, exit_date = t["entry_date"], t["exit_date"]
        if exit_date is None:
            exit_date = segment_end  # still-open position — exposed through segment end
        # only include trades whose lifespan overlaps this segment's window
        if exit_date < segment_start or entry_date > segment_end:
            continue
        positions_in_window.append({
            "instrument": t["instrument"],
            "entry_price": t["entry_price"],
            "notional_usd": t["notional_usd"],
            "start_date": max(entry_date, segment_start),
            "end_date": min(exit_date, segment_end),
        })

    if not positions_in_window:
        return []

    result = check_positions_against_m15(positions_in_window, account_balance, m15_data=m15_cache)
    return sorted(result["triggers"], key=lambda x: x["timestamp"])


def _run_segment(prepared, risk_per_trade, phase, starting_equity, start_date,
                  target_return=None, min_valid_days=0, payout_cycle=False,
                  aggregate_daily_risk_cap=None, instruments=None,
                  m15_cache=None, prior_goat_guard_strikes=0, sim_cache=None,
                  sizing_mode="constant", external_hwm=None):
    """
    Runs the trend engine from start_date forward and walks the daily
    return series applying governance day by day. Returns a dict
    describing the terminal outcome of this segment.

    outcome values: 'PASS' (evaluation target + min valid days both met),
    'PAYOUT' (funded-stage cycle hit its valid-day requirement),
    'NO_PAYOUT_CYCLE_END' (funded cycle ended without enough valid days —
    not a breach, just no payout that cycle, caller should re-call to
    continue), 'BREACH_DAILY', 'BREACH_STATIC', 'BREACH_GOAT_GUARD'
    (2nd cumulative Goat Guard trigger — hard breach, funded stage only),
    'DATA_EXHAUSTED'.

    For payout_cycle=True (funded stage) segments, this ALSO runs the
    real M15 Goat Guard check against this segment's actual trade log —
    not a placeholder. prior_goat_guard_strikes carries the cumulative
    count from earlier in the funded stage (Goat Guard strikes persist
    across the whole funded stage, not per-cycle, per GFT's real rule).
    Returns 'goat_guard_strikes' (updated cumulative count) in the result
    dict so the caller can track it across cycles.

    sim_cache: optional dict passed through to _get_cached_simulation()
    to avoid re-running the full historical simulation on every segment
    call. ONLY used under sizing_mode='constant' — see that mode's
    approximation notes. FORCIBLY BYPASSED under sizing_mode='cushion'
    (2026-09-02 fix), because caching a single shared full-history run
    and slicing it per segment is fundamentally incompatible with
    cushion sizing's path-dependent high-water-mark state — the cached
    run's HWM reflects a totally different (and wrong) equity trajectory
    than any individual real segment's own local history. Cushion mode
    always recomputes fresh, which is slower but correct.

    sizing_mode / external_hwm: passed through to simulate_portfolio().
    Under 'cushion', external_hwm must be threaded forward by the CALLER
    (run_full_lifecycle) from one segment's returned 'final_hwm' into the
    next segment's external_hwm argument — this function only computes
    and returns it, doesn't manage the cross-call bookkeeping itself.
    """
    instruments = instruments or INSTRUMENTS
    use_log = payout_cycle  # only need the trade log for Goat Guard checking

    if sizing_mode == "cushion":
        # Cache bypassed entirely — see docstring. Always fresh, always
        # exact, always requests the final HWM so the caller can carry
        # it into the next segment.
        sim_result = simulate_portfolio(
            prepared, risk_per_trade=risk_per_trade, phase=phase,
            starting_equity=starting_equity, aggregate_daily_risk_cap=aggregate_daily_risk_cap,
            instruments=instruments, log_trades=use_log,
            sizing_mode="cushion", external_hwm=external_hwm, return_final_hwm=True,
        )
        if use_log:
            full_returns, trade_log, final_hwm = sim_result
        else:
            full_returns, final_hwm = sim_result
            trade_log = None
    else:
        if sim_cache is not None:
            sim_result = _get_cached_simulation(
                prepared, risk_per_trade, phase, aggregate_daily_risk_cap,
                instruments, use_log, sim_cache,
            )
        else:
            sim_result = simulate_portfolio(
                prepared, risk_per_trade=risk_per_trade, phase=phase,
                starting_equity=starting_equity, aggregate_daily_risk_cap=aggregate_daily_risk_cap,
                instruments=instruments, log_trades=use_log,
            )
        if use_log:
            full_returns, trade_log = sim_result
        else:
            full_returns, trade_log = sim_result, None
        final_hwm = None  # unused under constant-risk sizing

    segment = full_returns[full_returns.index >= start_date]
    if len(segment) == 0:
        return {"outcome": "DATA_EXHAUSTED", "end_date": start_date,
                "ending_equity": starting_equity, "valid_days": 0, "days_elapsed": 0,
                "goat_guard_strikes": prior_goat_guard_strikes, "final_hwm": final_hwm}

    equity = starting_equity
    yesterday_equity = starting_equity  # basis for tomorrow's 4% daily-DD threshold
    valid_days = 0
    days_in_window = 0
    valid_day_dollar_threshold = ORIGINAL_ACCOUNT_BALANCE * VALID_DAY_MIN_PROFIT_PCT

    for date, r in segment.items():
        equity_before = equity
        equity = equity * (1 + r)
        days_in_window += 1

        daily_floor = yesterday_equity * (1 - DAILY_DD_PCT)
        if equity < daily_floor:
            return {"outcome": "BREACH_DAILY", "end_date": date, "ending_equity": equity,
                    "valid_days": valid_days, "days_elapsed": days_in_window,
                    "goat_guard_strikes": prior_goat_guard_strikes, "final_hwm": final_hwm}

        static_floor = ORIGINAL_ACCOUNT_BALANCE * (1 - STATIC_MAX_LOSS_PCT)
        if equity < static_floor:
            return {"outcome": "BREACH_STATIC", "end_date": date, "ending_equity": equity,
                    "valid_days": valid_days, "days_elapsed": days_in_window,
                    "goat_guard_strikes": prior_goat_guard_strikes, "final_hwm": final_hwm}

        dollar_pnl_today = equity - equity_before
        if dollar_pnl_today >= valid_day_dollar_threshold:
            valid_days += 1

        # GFT's rule: threshold recomputed daily from the HIGHER of
        # balance/equity at each 5PM snapshot. This engine doesn't
        # separate realized balance from mark-to-market equity, so both
        # are treated as equal to today's equity — see module docstring.
        yesterday_equity = equity

        if target_return is not None:
            cum_return = equity / starting_equity - 1
            if cum_return >= target_return and valid_days >= min_valid_days:
                return {"outcome": "PASS", "end_date": date, "ending_equity": equity,
                        "valid_days": valid_days, "days_elapsed": days_in_window,
                        "goat_guard_strikes": prior_goat_guard_strikes, "final_hwm": final_hwm}

        if payout_cycle and days_in_window >= PAYOUT_CYCLE_DAYS:
            # cycle window complete — check Goat Guard against this
            # segment's real trade log BEFORE resolving payout eligibility,
            # since a hard breach here overrides everything else
            cycle_start = segment.index[0]
            triggers = _check_goat_guard_for_segment(
                trade_log, cycle_start, date, starting_equity, m15_cache
            )
            cumulative_strikes = prior_goat_guard_strikes + len(triggers)
            if cumulative_strikes >= 2:
                return {"outcome": "BREACH_GOAT_GUARD", "end_date": date, "ending_equity": equity,
                        "valid_days": valid_days, "days_elapsed": days_in_window,
                        "goat_guard_strikes": cumulative_strikes, "final_hwm": final_hwm}

            if valid_days >= FUNDED_VALID_DAYS_PER_CYCLE:
                return {"outcome": "PAYOUT", "end_date": date, "ending_equity": equity,
                        "valid_days": valid_days, "days_elapsed": days_in_window,
                        "goat_guard_strikes": cumulative_strikes, "final_hwm": final_hwm}
            else:
                return {"outcome": "NO_PAYOUT_CYCLE_END", "end_date": date, "ending_equity": equity,
                        "valid_days": valid_days, "days_elapsed": days_in_window,
                        "goat_guard_strikes": cumulative_strikes, "final_hwm": final_hwm}

    return {"outcome": "DATA_EXHAUSTED", "end_date": segment.index[-1], "ending_equity": equity,
            "valid_days": valid_days, "days_elapsed": days_in_window,
            "goat_guard_strikes": prior_goat_guard_strikes, "final_hwm": final_hwm}



def run_full_lifecycle(prepared, risk_per_trade, start_date, aggregate_daily_risk_cap=None,
                        instruments=None, verbose=False, sim_cache=None, m15_cache=None,
                        sizing_mode="constant"):
    """
    Runs ONE account lifecycle: Step 1 -> Step 2 -> Funded (payout cycles
    until graduation, breach, or data exhaustion), starting from
    start_date with a fresh $5,000 evaluation account.

    Returns a structured dict with the full path: which stage it ended
    at, why, and the payout history if it reached funded.

    sim_cache / m15_cache: optional, passed through to avoid re-running
    the full historical simulation / reloading M15 data on every call —
    important when this function is called repeatedly (rebuy chaining,
    ensemble sweeps). If None, computes/loads fresh each time (slower,
    exact, backward compatible with existing direct calls). sim_cache is
    IGNORED under sizing_mode='cushion' (see _run_segment's docstring —
    caching is incompatible with path-dependent HWM state).

    sizing_mode: 'constant' (default, original behavior) or 'cushion'.
    Under 'cushion', this function tracks current_hwm across EVERY
    segment call (Step1 -> Step2 -> every funded cycle) and threads it
    through as each call's external_hwm — this is the actual fix for the
    HWM-persistence problem: the running high-water-mark now correctly
    spans the whole lifecycle, resetting only when a NEW call to this
    function starts a fresh rebuy attempt (current_hwm is initialized to
    ORIGINAL_ACCOUNT_BALANCE at the top of every call, exactly once per
    attempt).
    """
    log = []

    def _log(msg):
        if verbose:
            print(msg)
        log.append(msg)

    equity = ORIGINAL_ACCOUNT_BALANCE
    current_date = start_date
    # HWM state for sizing_mode='cushion' — resets fresh at the start of
    # every call to this function (i.e. every rebuy attempt), then
    # carried forward across every segment WITHIN this attempt via each
    # segment's returned 'final_hwm'. Unused under 'constant' mode.
    current_hwm = ORIGINAL_ACCOUNT_BALANCE

    # --- Step 1 ---
    r = _run_segment(prepared, risk_per_trade, "evaluation", equity, current_date,
                      target_return=STEP1_TARGET, min_valid_days=MIN_VALID_DAYS_EVAL,
                      aggregate_daily_risk_cap=aggregate_daily_risk_cap, instruments=instruments,
                      sim_cache=sim_cache, sizing_mode=sizing_mode, external_hwm=current_hwm)
    _log(f"Step 1: {r['outcome']} at {r['end_date']}, equity=${r['ending_equity']:.2f}, "
         f"{r['days_elapsed']} days, {r['valid_days']} valid days")
    if sizing_mode == "cushion":
        current_hwm = r["final_hwm"]
    if r["outcome"] != "PASS":
        return {"final_status": f"FAILED_STEP1_{r['outcome']}", "payouts": [], "log": log,
                "end_date": r["end_date"], "ending_equity": r["ending_equity"]}
    # BUGFIX: next segment must start the day AFTER this one ends, not on
    # the same date. _run_segment slices with `>= start_date`, so passing
    # the same end_date back in would apply that day's return TWICE —
    # once as the terminal day of this segment, again as day 1 of the
    # next. Applies at every transition in this function (Step1->Step2,
    # Step2->Funded, and every funded-cycle boundary below), all fixed
    # the same way, matching the +1-day pattern already correctly used
    # in run_lifecycle_chain's between-ATTEMPT rebuy logic.
    equity, current_date = r["ending_equity"], r["end_date"] + pd.Timedelta(days=1)

    # --- Step 2 ---
    r = _run_segment(prepared, risk_per_trade, "evaluation", equity, current_date,
                      target_return=STEP2_TARGET, min_valid_days=MIN_VALID_DAYS_EVAL,
                      aggregate_daily_risk_cap=aggregate_daily_risk_cap, instruments=instruments,
                      sim_cache=sim_cache, sizing_mode=sizing_mode, external_hwm=current_hwm)
    _log(f"Step 2: {r['outcome']} at {r['end_date']}, equity=${r['ending_equity']:.2f}, "
         f"{r['days_elapsed']} days, {r['valid_days']} valid days")
    if sizing_mode == "cushion":
        current_hwm = r["final_hwm"]
    if r["outcome"] != "PASS":
        return {"final_status": f"FAILED_STEP2_{r['outcome']}", "payouts": [], "log": log,
                "end_date": r["end_date"], "ending_equity": r["ending_equity"]}
    equity, current_date = r["ending_equity"], r["end_date"] + pd.Timedelta(days=1)

    # --- Funded: loop payout cycles until graduation, breach, or data runs out ---
    # M15 data loaded ONCE per lifecycle, LAZILY — only if a funded
    # segment is actually reached. Mutates the passed-in dict in place
    # (rather than reassigning) so a caller sharing one cache dict across
    # multiple chained lifecycle attempts (run_lifecycle_chain) sees the
    # same loaded data reused, not reloaded per attempt.
    if m15_cache is None:
        m15_cache = {}
    if not m15_cache:
        m15_cache.update({inst: load_m15(inst) for inst in (instruments or INSTRUMENTS)})
    payouts = []
    goat_guard_strikes = 0
    while len(payouts) < GRADUATION_PAYOUT_COUNT:
        cycle_start_equity = equity  # captured BEFORE this cycle runs, needed
                                       # to compute the cycle's actual profit
                                       # for real dollar payout accounting
        r = _run_segment(prepared, risk_per_trade, "funded", equity, current_date,
                          payout_cycle=True, aggregate_daily_risk_cap=aggregate_daily_risk_cap,
                          instruments=instruments, m15_cache=m15_cache,
                          prior_goat_guard_strikes=goat_guard_strikes, sim_cache=sim_cache,
                          sizing_mode=sizing_mode, external_hwm=current_hwm)
        goat_guard_strikes = r["goat_guard_strikes"]
        if sizing_mode == "cushion":
            current_hwm = r["final_hwm"]
        _log(f"Funded cycle: {r['outcome']} at {r['end_date']}, equity=${r['ending_equity']:.2f}, "
             f"{r['valid_days']} valid days, goat_guard_strikes={goat_guard_strikes}")

        if r["outcome"] == "PAYOUT":
            payout_index = len(payouts) + 1
            cycle_profit = r["ending_equity"] - cycle_start_equity
            payout_amount = compute_payout_dollar_amount(payout_index, cycle_profit)
            # BUGFIX: the withdrawn payout must be DEDUCTED from equity
            # before it carries into the next cycle — otherwise every
            # subsequent daily/static/Goat-Guard check (all of which key
            # off the carried-forward equity value) sees an inflated
            # balance that was never really there, making the account
            # look more resilient post-payout than it actually would be.
            equity = r["ending_equity"] - payout_amount
            current_date = r["end_date"] + pd.Timedelta(days=1)
            # NOTE: current_hwm is NOT reduced by the payout withdrawal
            # here — a genuine, stated judgment call, not an oversight.
            # The cushion mechanism protects against DRAWDOWN (equity
            # falling below its own peak), and a payout withdrawal isn't
            # a drawdown in that sense — it's a deliberate transfer out,
            # not the strategy losing money. Reducing HWM in step with a
            # withdrawal would make the mechanism MORE lenient right
            # after every payout, which isn't obviously correct either.
            # Flagged for revisit if real behavior around this matters.
            payouts.append({
                "date": r["end_date"],
                "equity_at_payout": r["ending_equity"],  # PRE-withdrawal, for the record
                "equity_after_payout": equity,             # POST-withdrawal, what actually carries forward
                "cycle_start_equity": cycle_start_equity,
                "cycle_profit": cycle_profit,
                "payout_amount": payout_amount,  # computed ONCE, here — downstream
                                                   # consumers (ensemble_ev.py) should
                                                   # read this directly rather than
                                                   # recomputing it, avoiding any risk
                                                   # of the two calculations drifting apart
                "payout_index": payout_index,
            })
        elif r["outcome"] == "NO_PAYOUT_CYCLE_END":
            equity, current_date = r["ending_equity"], r["end_date"] + pd.Timedelta(days=1)
            continue  # missed this cycle, keep going — not a breach
        else:
            return {"final_status": f"FUNDED_{r['outcome']}", "payouts": payouts, "log": log,
                    "end_date": r["end_date"], "ending_equity": r["ending_equity"],
                    "goat_guard_strikes": goat_guard_strikes}

    return {"final_status": "GRADUATED", "payouts": payouts, "log": log,
            "end_date": payouts[-1]["date"], "ending_equity": equity,
            "goat_guard_strikes": goat_guard_strikes}


ACCOUNT_COST_USD = 36.0  # confirmed via direct checkout, see decisions_log.md

PAYOUT_SPLIT_PCT = 0.80  # confirmed
PAYOUT_CAP_PCT_FIRST_TWO = 0.04  # confirmed — $5k-specific exception (not the
                                    # standard 6%), accounts purchased >= 2026-07-25
PAYOUT_CAP_PCT_LATER = 0.04  # UNCONFIRMED — the FAQ only specifies the cap for
                               # the "initial 2 payouts." What applies to payout
                               # #3 onward (relevant here, since graduation is
                               # exactly 3 payouts) was NOT found in the pulled
                               # FAQ text. Conservatively assumed equal to the
                               # first-two cap rather than uncapped — an
                               # unconfirmed optimistic assumption would
                               # overstate EV, which is the more dangerous
                               # direction to be wrong in. Flagged here and in
                               # decisions_log.md as needing real confirmation.


def compute_payout_dollar_amount(payout_index: int, cycle_profit: float) -> float:
    """
    Real dollar payout for one cycle: 80% of that cycle's profit, capped.
    Returns 0.0 for a non-positive cycle_profit (can't pay out a loss).

    KNOWN SIMPLIFICATION, not yet corrected: this function computes what
    a payout is WORTH, but run_full_lifecycle's funded-stage equity path
    does NOT currently deduct withdrawn payouts from simulated equity —
    equity continues compounding as if the payout were never withdrawn.
    Real accounts almost certainly reduce balance/equity by the
    withdrawal amount, which would affect subsequent cycles' daily/static
    drawdown checks. This is a real, unaddressed gap — flagged here and
    in decisions_log.md, not silently glossed over. For a first pass at
    ensemble EV this is a reasonable simplification (it makes the account
    look slightly more resilient post-payout than it truly would be), but
    should be fixed before treating results as final.
    """
    if cycle_profit <= 0:
        return 0.0
    cap_pct = PAYOUT_CAP_PCT_FIRST_TWO if payout_index <= 2 else PAYOUT_CAP_PCT_LATER
    cap_dollar = cap_pct * ORIGINAL_ACCOUNT_BALANCE
    return min(cycle_profit * PAYOUT_SPLIT_PCT, cap_dollar)


def run_lifecycle_chain(prepared, risk_per_trade, start_date, wall_clock_budget_days,
                          aggregate_daily_risk_cap=None, instruments=None, verbose=False,
                          sim_cache=None, m15_cache=None, sizing_mode="constant"):
    """
    The actual rebuy loop: repeatedly attempts fresh $5,000 account
    lifecycles (each costing ACCOUNT_COST_USD), starting from start_date
    and continuing CHRONOLOGICALLY FORWARD through real calendar dates —
    not synthetic bootstrap. See decisions_log.md, "quant call: real
    calendar-anchored sweep, not statistical bootstrap" for why: block-
    bootstrapping would break Goat Guard's exact M15 reconstruction,
    which has no meaning for a synthetic date that never existed.

    On any breach, immediately "rebuys" (starts a fresh attempt) from
    the day after the breach date, continuing forward in real time,
    until EITHER graduation is reached, the wall-clock budget is
    exhausted, or real historical data runs out (whichever comes first).
    Each fresh attempt gets its OWN fresh HWM (current_hwm resets to
    ORIGINAL_ACCOUNT_BALANCE at the top of run_full_lifecycle every
    time) — correct under sizing_mode='cushion', since a genuine new
    account purchase should not inherit the previous attempt's HWM.

    Returns a dict: total attempts, total cost, whether graduation was
    reached, all individual attempt outcomes, and the reason chaining
    stopped (GRADUATED / BUDGET_EXHAUSTED / DATA_EXHAUSTED).

    sim_cache / m15_cache: optional, shared across MULTIPLE calls to this
    function (not just within one chain) — the full historical return
    series and M15 data only depend on risk_per_trade, not on
    start_date, so a caller sweeping many starting points at the SAME
    risk level (ensemble_ev.py) should build these once and pass them
    into every call, rather than letting each call rebuild its own from
    scratch. If None, builds fresh, scoped to just this one chain
    (original behavior, correct but slower for a sweep). sim_cache is
    IGNORED under sizing_mode='cushion' regardless of what's passed —
    see _run_segment's docstring for why caching is incompatible with
    cushion sizing's path-dependent state. Expect sizing_mode='cushion'
    sweeps to be meaningfully slower than 'constant' ones as a direct
    consequence — a real, accepted cost of correctness, not an oversight.

    sizing_mode: 'constant' (default) or 'cushion', passed straight
    through to every run_full_lifecycle() call in this chain.
    """
    instruments = instruments or INSTRUMENTS
    if sim_cache is None:
        sim_cache = {}  # shared across every attempt in this chain — same
                          # risk_per_trade throughout, so the cached full
                          # historical return series is valid for all of them
                          # (constant-risk mode only — ignored under cushion)
    if m15_cache is None:
        m15_cache = {}  # shared, LAZILY populated — only loaded if/when some
                          # attempt in this chain actually reaches the funded
                          # stage. A chain where every attempt breaches during
                          # evaluation never touches M15 data at all.

    budget_end_date = start_date + pd.Timedelta(days=wall_clock_budget_days)
    attempts = []
    total_cost = 0.0
    current_date = start_date
    graduated = False

    while current_date < budget_end_date:
        total_cost += ACCOUNT_COST_USD
        result = run_full_lifecycle(
            prepared, risk_per_trade, current_date,
            aggregate_daily_risk_cap=aggregate_daily_risk_cap, instruments=instruments,
            verbose=verbose, sim_cache=sim_cache, m15_cache=m15_cache,
            sizing_mode=sizing_mode,
        )
        attempts.append(result)

        if result["final_status"] == "GRADUATED":
            graduated = True
            break

        if "DATA_EXHAUSTED" in result["final_status"]:
            return {"attempts": attempts, "total_cost": total_cost, "graduated": False,
                    "stopped_reason": "DATA_EXHAUSTED", "n_attempts": len(attempts)}

        # breach of some kind — rebuy starting the day after where this
        # attempt ended, continuing forward in real calendar time
        current_date = result["end_date"] + pd.Timedelta(days=1)

    return {"attempts": attempts, "total_cost": total_cost, "graduated": graduated,
            "stopped_reason": "GRADUATED" if graduated else "BUDGET_EXHAUSTED",
            "n_attempts": len(attempts)}


if __name__ == "__main__":
    from p32_signal_prep import load_and_prep_all

    print("Loading and prepping signal data...")
    prepared = load_and_prep_all(instruments=INSTRUMENTS)

    print("\n=== Smoke test: single lifecycle, risk_per_trade=0.008, "
          "starting from earliest common date ===\n")
    common_start = max(prepared[i].dropna(subset=["atr20", "signal"]).index.min() for i in INSTRUMENTS)

    result = run_full_lifecycle(prepared, risk_per_trade=0.008, start_date=common_start, verbose=True)

    print(f"\nFinal status: {result['final_status']}")
    print(f"Payouts achieved: {len(result['payouts'])}")
    for p in result["payouts"]:
        print(f"  {p['date']}: equity=${p['equity_at_payout']:.2f}")
    print(f"End date: {result['end_date']} | Ending equity: ${result['ending_equity']:.2f}")
    print("\nThis is a SMOKE TEST of the state machine on ONE historical path — "
          "not a Monte Carlo, not an ensemble-EV result. This path never "
          "reaches the funded stage, so it does NOT exercise the Goat Guard "
          "wiring — see goat_guard_wiring_test.py for that.")

    print("\n\n=== Chain smoke test: rebuy loop, risk_per_trade=0.008, "
          "3-year wall-clock budget, starting from earliest common date ===\n")
    chain_result = run_lifecycle_chain(
        prepared, risk_per_trade=0.008, start_date=common_start,
        wall_clock_budget_days=365 * 3, verbose=False,
    )
    print(f"Stopped reason: {chain_result['stopped_reason']}")
    print(f"Attempts: {chain_result['n_attempts']}")
    print(f"Total cost: ${chain_result['total_cost']:.2f}")
    print(f"Graduated: {chain_result['graduated']}")
    for i, a in enumerate(chain_result["attempts"]):
        print(f"  Attempt {i+1}: {a['final_status']} at {a['end_date']}, "
              f"ending equity ${a['ending_equity']:.2f}")
    print("\nThis exercises the REBUY LOOP specifically — multiple chained "
          "attempts over real calendar time, cost accounted per attempt. "
          "Still a single starting point, not the full ensemble sweep "
          "across many starting points and risk levels (ensemble_ev.py, "
          "not yet built).")