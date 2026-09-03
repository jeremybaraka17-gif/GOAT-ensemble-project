"""
p32_risk_governor.py — tracks the LIVE GOAT 2-Step $5k account's state
against GFT's actual confirmed rules (see research/decisions_log.md for
the full confirmation trail) and produces governor states that
p32_signal_workflow.py (not yet built) will consult before suggesting any
new entry, exactly mirroring prop_firm_project/live/risk_governor.py's
proven pattern for the 1-Step $10k account — but this account has real
structural differences that pattern doesn't cover, all handled here:

  1. MULTI-STAGE: Step 1 -> Step 2 -> Funded, each with its own profit
     target (8% / 6% / none) and its own valid-trading-day requirement.
     prop_firm_project's account is single-stage.
  2. GOAT GUARD: a SECOND, INDEPENDENT governor layer that only exists at
     the funded stage — continuous combined floating-loss monitoring
     (2% of balance), soft-then-hard breach, persists cumulatively across
     the whole funded stage. prop_firm_project's account has no
     equivalent mechanism at all.
  3. VALID-DAY / PAYOUT-CYCLE TRACKING: funded-stage payout eligibility
     depends on counting real trading days meeting a profit threshold
     within a 14-day cycle — a genuinely new kind of state prop_firm_
     project's governor never needed to track.

Same core safety lesson already learned and DESIGNED IN from the start
here, not discovered as a bug later: HALT means "stop NEW positions,
existing may remain" — never a forced-liquidation trigger. See
xau_xag_project's HALT-liquidation bugfix and prop_firm_project's
risk_governor.py docstring for why this matters.

State is persisted to p32_governor_state.json. Thresholds below carry a
deliberate safety buffer under GFT's actual confirmed hard rules — NOT
backtested or tuned, matching the same judgment-call convention used for
every other buffer in this project family.
"""
import json
import os
from datetime import date, datetime
from enum import Enum

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "p32_governor_state.json")

# Confirmed GOAT Model rules — see research/decisions_log.md for the
# dated confirmation trail on every number below. NOT the same as
# prop_firm_project's 1-Step $10k rules (3% daily / 6% static) — this is
# a genuinely different account and ruleset.
INITIAL_ACCOUNT_BALANCE = 5000.0
DAILY_DD_HARD_LIMIT_PCT = 0.04
STATIC_MAX_LOSS_HARD_LIMIT_PCT = 0.10
STEP1_TARGET_PCT = 0.08
STEP2_TARGET_PCT = 0.06
MIN_VALID_DAYS_EVAL = 3
VALID_DAY_MIN_PROFIT_PCT = 0.005  # of INITIAL_ACCOUNT_BALANCE, confirmed FAQ wording
FUNDED_VALID_DAYS_PER_CYCLE = 4    # this account purchased >= 2026-07-25
PAYOUT_CYCLE_DAYS = 14
GRADUATION_PAYOUT_COUNT = 3         # locked

# Safety buffers under the hard limits — same convention as
# prop_firm_project's risk_governor.py: WARNING at 70% of the hard limit,
# HALT at 90%. Judgment calls, not backtested.
DAILY_WARNING_PCT = DAILY_DD_HARD_LIMIT_PCT * 0.70
DAILY_HALT_PCT = DAILY_DD_HARD_LIMIT_PCT * 0.90
STATIC_WARNING_PCT = STATIC_MAX_LOSS_HARD_LIMIT_PCT * 0.70
STATIC_HALT_PCT = STATIC_MAX_LOSS_HARD_LIMIT_PCT * 0.90

# Goat Guard — confirmed via live-account FAQ pull, see decisions_log.md.
# NOT buffered below the hard 2% threshold the way daily/static DD are —
# Goat Guard is checked continuously in real time (not once a day), so a
# buffer serves a different purpose here: WARNING gives early notice to
# consider manually reducing exposure, HALT (at the real 2% line) blocks
# new entries. The hard breach consequence itself (split cut, or account
# closure) is GFT's own enforcement, not something this governor decides.
GOAT_GUARD_WARNING_PCT = 0.015  # 75% of the real 2% threshold
GOAT_GUARD_HARD_PCT = 0.02       # the real, confirmed threshold


class GovernorState(Enum):
    NORMAL = "normal"
    WARNING = "warning"
    HALT = "halt"


class Stage(Enum):
    STEP1 = "step1"
    STEP2 = "step2"
    FUNDED = "funded"


def _load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return None


def _save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def initialize_challenge(force: bool = False):
    """Run ONCE, manually, at the start of a challenge attempt — or
    again, deliberately, after a breach when a fresh account is
    purchased (a genuine new attempt, not a bug to guard against the way
    prop_firm_project's version does for a single long-lived account).
    Refuses to silently overwrite existing state unless force=True."""
    existing = _load_state()
    if existing and not force:
        raise RuntimeError(
            f"Governor state already initialized (stage={existing['stage']}, "
            f"started {existing['challenge_start_date']}). Pass force=True if "
            f"you're deliberately starting a NEW challenge attempt (e.g. after "
            f"a breach and rebuy)."
        )
    today = date.today().isoformat()
    state = {
        "stage": Stage.STEP1.value,
        "challenge_start_date": today,
        "stage_start_balance": INITIAL_ACCOUNT_BALANCE,
        "day_start_reference": INITIAL_ACCOUNT_BALANCE,  # higher of balance/equity at last 5PM EST snapshot
        "last_recorded_date": today,
        "valid_days_count_current_cycle": 0,
        "current_cycle_start_date": today,
        "payouts_received": 0,
        "goat_guard_strikes": 0,
        "goat_guard_split_reduced": False,  # True once the 1st Goat Guard
                                              # strike has permanently cut
                                              # the profit split to 50%
    }
    _save_state(state)
    return state


def evaluate_daily_static(current_balance: float, current_equity: float):
    """
    Call this once per trading day (matches the 5PM EST snapshot
    convention as closely as a once-daily check reasonably can — see the
    same approximation gap already flagged in prop_firm_project's
    risk_governor.py docstring; this script approximates "day start" as
    first-run-of-the-calendar-day, not the exact 5PM EST cutoff).

    Returns (GovernorState, dict of diagnostics). Updates and persists
    day_start_reference automatically when the calendar date rolls over,
    using the confirmed real rule: the HIGHER of balance or equity at
    that snapshot, not just balance alone.
    """
    state = _load_state()
    if state is None:
        raise RuntimeError(
            "Governor not initialized. Run initialize_challenge() once before using this."
        )

    today = date.today().isoformat()
    if state["last_recorded_date"] != today:
        state["day_start_reference"] = max(current_balance, current_equity)
        state["last_recorded_date"] = today

    _save_state(state)

    daily_loss_pct = (state["day_start_reference"] - current_equity) / state["day_start_reference"] \
        if state["day_start_reference"] > 0 else 0.0
    static_loss_pct = (INITIAL_ACCOUNT_BALANCE - current_equity) / INITIAL_ACCOUNT_BALANCE

    if daily_loss_pct >= DAILY_HALT_PCT or static_loss_pct >= STATIC_HALT_PCT:
        gstate = GovernorState.HALT
    elif daily_loss_pct >= DAILY_WARNING_PCT or static_loss_pct >= STATIC_WARNING_PCT:
        gstate = GovernorState.WARNING
    else:
        gstate = GovernorState.NORMAL

    diagnostics = {
        "stage": state["stage"],
        "day_start_reference": state["day_start_reference"],
        "current_equity": current_equity,
        "daily_loss_pct": daily_loss_pct,
        "daily_hard_limit_pct": DAILY_DD_HARD_LIMIT_PCT,
        "daily_room_remaining_pct": DAILY_DD_HARD_LIMIT_PCT - daily_loss_pct,
        "static_loss_pct": static_loss_pct,
        "static_hard_limit_pct": STATIC_MAX_LOSS_HARD_LIMIT_PCT,
        "static_room_remaining_pct": STATIC_MAX_LOSS_HARD_LIMIT_PCT - static_loss_pct,
        "profit_pct_this_stage": (current_equity - state["stage_start_balance"]) / state["stage_start_balance"],
    }
    return gstate, diagnostics


def record_valid_day_if_applicable(dollar_pnl_today: float):
    """
    Call once per day (after the day's trading is done, alongside
    evaluate_daily_static) — checks whether today counts as a "valid
    trading day" (>=0.5% of INITIAL balance profit, confirmed FAQ
    wording — NOT relative to current equity) and updates the running
    count used for both evaluation-stage min-days and funded-stage
    payout-cycle eligibility.
    """
    state = _load_state()
    if state is None:
        raise RuntimeError("Governor not initialized.")

    threshold = INITIAL_ACCOUNT_BALANCE * VALID_DAY_MIN_PROFIT_PCT
    if dollar_pnl_today >= threshold:
        state["valid_days_count_current_cycle"] += 1
        _save_state(state)
    return state["valid_days_count_current_cycle"]


def check_stage_progress(current_equity: float):
    """
    Call after evaluate_daily_static and record_valid_day_if_applicable
    each day. Checks whether the CURRENT stage's target + min-valid-days
    are BOTH met (Step1/Step2) — if so, advances to the next stage and
    resets stage-specific state. Funded stage has no profit target, so
    this function only checks payout-cycle progress for that stage (see
    check_payout_cycle below) — call that one instead once stage=='funded'.

    Returns (advanced: bool, new_stage_or_None, diagnostics).
    """
    state = _load_state()
    if state is None:
        raise RuntimeError("Governor not initialized.")

    stage = state["stage"]
    if stage == Stage.FUNDED.value:
        return False, None, {"note": "funded stage has no profit target — use check_payout_cycle instead"}

    target = STEP1_TARGET_PCT if stage == Stage.STEP1.value else STEP2_TARGET_PCT
    profit_pct = (current_equity - state["stage_start_balance"]) / state["stage_start_balance"]
    valid_days = state["valid_days_count_current_cycle"]

    diagnostics = {
        "stage": stage, "profit_pct": profit_pct, "target_pct": target,
        "valid_days": valid_days, "min_valid_days": MIN_VALID_DAYS_EVAL,
    }

    if profit_pct >= target and valid_days >= MIN_VALID_DAYS_EVAL:
        next_stage = Stage.STEP2.value if stage == Stage.STEP1.value else Stage.FUNDED.value
        state["stage"] = next_stage
        state["stage_start_balance"] = current_equity
        state["valid_days_count_current_cycle"] = 0
        state["current_cycle_start_date"] = date.today().isoformat()
        _save_state(state)
        return True, next_stage, diagnostics

    return False, None, diagnostics


def check_payout_cycle():
    """
    Call once per day once stage=='funded'. Checks whether the current
    14-day payout cycle has ended and, if so, whether it met the
    valid-day requirement. INTERPRETATION FLAGGED, not independently
    confirmed beyond the FAQ (see research/decisions_log.md, "payout-
    cycle-boundary interpretation"): evaluated at the END of each fixed
    14-day window, not continuously as valid days accumulate.

    Returns a dict: {'cycle_ended': bool, 'payout_eligible': bool or None,
    'valid_days': int, 'days_in_cycle': int}. Caller is responsible for
    actually confirming and processing a real payout through GFT's
    interface — this function only tracks eligibility, it has no way to
    submit a withdrawal request itself.
    """
    state = _load_state()
    if state is None:
        raise RuntimeError("Governor not initialized.")
    if state["stage"] != Stage.FUNDED.value:
        raise RuntimeError("check_payout_cycle() only applies once stage=='funded'.")

    cycle_start = datetime.fromisoformat(state["current_cycle_start_date"]).date()
    days_in_cycle = (date.today() - cycle_start).days
    valid_days = state["valid_days_count_current_cycle"]

    if days_in_cycle < PAYOUT_CYCLE_DAYS:
        return {"cycle_ended": False, "payout_eligible": None,
                "valid_days": valid_days, "days_in_cycle": days_in_cycle}

    eligible = valid_days >= FUNDED_VALID_DAYS_PER_CYCLE
    if eligible:
        state["payouts_received"] += 1
    state["valid_days_count_current_cycle"] = 0
    state["current_cycle_start_date"] = date.today().isoformat()
    _save_state(state)

    return {"cycle_ended": True, "payout_eligible": eligible,
            "valid_days": valid_days, "days_in_cycle": days_in_cycle,
            "total_payouts_received": state["payouts_received"],
            "graduated": state["payouts_received"] >= GRADUATION_PAYOUT_COUNT}


def check_goat_guard(combined_floating_pnl: float, current_balance: float):
    """
    Call FREQUENTLY (this is a continuous, real-time GFT rule — not a
    once-daily check like daily/static DD). Only meaningful once
    stage=='funded'; calling this during Step1/Step2 is a no-op check
    returned for informational purposes only, since Goat Guard doesn't
    actually apply there.

    combined_floating_pnl: sum of unrealized P&L (in account currency,
    signed — losses negative) across ALL currently open positions, read
    directly from MT5, not derived from anything this governor tracks
    itself.

    Strike tracking persists CUMULATIVELY across the whole funded stage,
    matching GFT's real rule (1st = soft breach, permanent 50% split cut;
    2nd = hard breach, account closed) — NOT reset per payout cycle.

    Returns (GovernorState, dict of diagnostics). HALT means block new
    entries only — a Goat Guard hard breach closing the account is GFT's
    own action, not something this function performs; existing open
    positions are GFT's problem to close, not this script's.
    """
    state = _load_state()
    if state is None:
        raise RuntimeError("Governor not initialized.")

    if state["stage"] != Stage.FUNDED.value:
        return GovernorState.NORMAL, {"note": "Goat Guard does not apply outside the funded stage",
                                        "stage": state["stage"]}

    floating_loss_pct = -combined_floating_pnl / current_balance if current_balance > 0 else 0.0

    diagnostics = {
        "combined_floating_pnl": combined_floating_pnl,
        "floating_loss_pct": floating_loss_pct,
        "hard_threshold_pct": GOAT_GUARD_HARD_PCT,
        "cumulative_strikes_before_this_check": state["goat_guard_strikes"],
        "split_already_reduced": state["goat_guard_split_reduced"],
    }

    if floating_loss_pct >= GOAT_GUARD_HARD_PCT:
        state["goat_guard_strikes"] += 1
        if state["goat_guard_strikes"] == 1:
            state["goat_guard_split_reduced"] = True
            diagnostics["consequence"] = "1st strike — SOFT breach: profit split permanently cut to 50%. Positions remain open."
        else:
            diagnostics["consequence"] = "2nd+ strike — HARD breach: GFT will close the account. This governor cannot prevent that, only flag it."
        _save_state(state)
        diagnostics["cumulative_strikes_after_this_check"] = state["goat_guard_strikes"]
        return GovernorState.HALT, diagnostics

    if floating_loss_pct >= GOAT_GUARD_WARNING_PCT:
        return GovernorState.WARNING, diagnostics

    return GovernorState.NORMAL, diagnostics


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Initialize or inspect the P32 GOAT account governor state.")
    parser.add_argument("--init", action="store_true", help="Initialize governor at Step 1 (run once, at challenge start)")
    parser.add_argument("--force", action="store_true", help="Allow overwriting existing state with --init")
    parser.add_argument("--show", action="store_true", help="Print current raw state file contents")
    args = parser.parse_args()

    if args.init:
        state = initialize_challenge(force=args.force)
        print(f"Governor initialized: {state}")
    elif args.show:
        state = _load_state()
        print(json.dumps(state, indent=2) if state else "No governor state found — run with --init first.")
    else:
        parser.print_help()