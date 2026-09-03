# P32 — GOAT 2-Step $5k Ensemble-EV Model (DRAFT — NOT YET LOCKED)

**Status: BLOCKED. Do not run any simulation against this file until every
field marked BLOCKED below is resolved and this status line is changed to
LOCKED with a date.**

**Reference:** `prop_firm_project`'s Arm CC engine (200-day MA signal,
2xATR(20) Chandelier trailing stop, cushion-proportional sizing) as the
starting architecture; P31 (`hypothesis_p31_ensemble_ev_risk_mapping.md`)
as the objective-function template, adapted here to a multi-stage account
structure P31 didn't need to model; `research/decisions_log.md` for the
full reasoning trail behind every number below.

## Why this is a different hypothesis from P31, not a re-run of it
P31 was scoped for the 1-Step $10k account: a single pass/breach binary
per simulated path. This account is 2-Step: Step 1 -> Step 2 -> funded,
each with its own pass condition, and the funded stage carries an
additional continuous risk mechanism (Goat Guard) P31's account doesn't
have at all. This requires a genuine multi-stage sequential model, not a
parameter swap on P31's existing code.

## Locked / confirmed inputs (see decisions_log.md for sourcing)
- Account: GFT 2-Step GOAT Model, $5,000
- Step 1 target: 8% | Step 2 target: 6% | Daily DD: 4% (all stages) |
  Max static loss: 10%
- Valid trading day: profit >= 0.5% of initial balance that day
- Funded payout eligibility: 3 valid days per cycle (4 if purchased on/after
  2026-07-25), resets each payout cycle
- Payout cycle: bi-weekly (14 days) | Profit split: 80% | Daily profit cap
  (funded only): $3,000
- Initial-2-payout withdrawal cap: 4% of initial balance (not 6% — the
  $5k-specific exception), lifts after 2nd successful payout
- Leverage: Forex 1:100 eval / 1:50 funded; Indices/Commodities 1:20 eval /
  1:10 funded; Crypto 1:2 both phases
- Goat Guard (funded stage only): combined floating loss across all open
  positions >= 2% of account balance triggers. 1st trigger = soft (split
  cut to 50%, permanent, account stays open). 2nd trigger = hard breach,
  account closed.
- Goat Guard modeling method: M15 intrabar reconstruction (daily-HL
  rejected — see decisions_log.md for the false-synchronization reasoning)
- Graduation trigger: 3 successful funded-stage payouts (LOCKED, stated
  before any simulation, per project convention)
- Account cost: ~$18 (UNCONFIRMED PRECISION — model both a base-price and
  a promo-price scenario until resolved, do not collapse to one number)

## BLOCKED — required before this hypothesis can be locked and run
1. **Instrument universe at $5k.** NOT assumed to be the existing
   7-instrument set validated at $10k. Requires confirmed $5k GOAT-model
   contract specs (min lot, contract size, margin) pulled directly from a
   purchased account or GFT's specification sheet for this exact model —
   not inferred from the 1-Step $10k specs already coded elsewhere.
2. **M15 price history for the candidate universe.** Pull script drafted
   at `data/pull_m15_history.py`. Actual returned depth must be checked
   and recorded, not assumed.
3. **Graduation buffer multiple.** 3 payouts is locked as the trigger
   count, but if the eventual design also wants a dollar-based
   confirmation (e.g., cumulative realized payouts vs. account cost), that
   multiple needs to be chosen and stated now if used at all — not decided
   after seeing simulated results.
4. **Account cost precision** — base vs. promotional price.

## Claim (draft — subject to revision once blocked items resolve)
There exists a base-risk level R (and a resulting cushion-sizing
calibration) for this account structure that maximizes net ensemble EV per
dollar of account cost over a repeated rebuy loop, incorporating Goat
Guard breach risk as a real, modeled cost — not a footnote. Per the
literature reviewed in this project's design conversation (Carpenter 2000;
Guasoni; Panageas & Westerfield 2009 on risk-shifting incentives under
option-like, finite- vs. long-horizon compensation), this R is NOT assumed
in advance to be higher than the existing Arm CC calibration (0.8%,
P14/P15) — the repeated-game, multi-payout structure here is exactly the
condition under which that literature finds the risk-shifting incentive
weakens, not strengthens. Whether R turns out higher, lower, or
statistically indistinguishable from Arm CC's existing calibration is the
actual open question this hypothesis exists to answer.

## Falsification condition (draft — finalize when locking)
To be written in the same form as P31's: an explicit R* decision rule
(passes / fails / inconclusive) against a fixed, pre-stated risk grid, with
Goat Guard-driven breach probability included as a modeled cost in the
ensemble-EV objective, not bolted on afterward.

## Two-attempt-then-stop
Applies. One pre-registered test of this specific account structure and
objective. If it fails or is inconclusive, the question is closed under
this exact framing — a different framing (e.g., a different account model
or objective) would be a new, separately-justified hypothesis, not a
re-run of this one with adjusted inputs.
