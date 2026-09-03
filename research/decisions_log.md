# Decisions Log — goat_5k_ensemble_project

Running, dated log of every parameter confirmed or locked for this project,
with its source. Nothing in `config/strategy.yaml` should exist without an
entry here explaining where it came from. Mirrors the addendum discipline
used in `prop_firm_project` and `xau_xag_project`, but kept as a single
running log rather than numbered addenda since this project hasn't run
a backtest yet — numbered addenda start once real results exist.

---

## 2026-09-01 — Account model selection
**Decision:** GOAT Model (not Standard), 2-Step, $5,000 account size.
**Source:** user decision.

## 2026-09-01 — Evaluation phase rules (CONFIRMED against primary source)
Pulled directly from `help.goatfundedtrader.com/en/articles/13575348-2-step-goat-model`
(page timestamp: "Updated over 2 weeks ago" as of pull date). This
supersedes an earlier user-supplied summary sourced partly from a Scribd
document — the Scribd figures happened to match on the numbers checked,
but the primary source is now the source of record going forward.

| | Step 1 | Step 2 | Step 3 (Funded) |
|---|---|---|---|
| Profit Target | 8% | 6% | None |
| Daily Drawdown | 4% | 4% | 4% |
| Max Loss | 10% (static) | 10% (static) | 10% (static) |
| Min Trading Days | 3 | 3 | 3 per payout (4 for accounts purchased ≥ 2026-07-25) |
| Max Daily Profit | No limit | No limit | $3,000 |
| Goat Guard | No | No | Yes |

**Daily drawdown calc mechanic:** at 5:00 PM EST, take the HIGHER of
account balance or equity, subtract 4% of that value — that's the floor
for the next trading day. NOT a simple day-start-balance check; matches
the "5PM EST snapshot" nuance already flagged as an approximation gap in
`prop_firm_project/live/risk_governor.py`'s own docstring.

**"Valid trading day" definition:** a day where profit made that day is
≥0.5% of initial account balance. This is NOT the same as "a day where a
trade was placed" — matters directly for simulating funded-stage payout
eligibility, which is NOT a simple calendar trigger.

## 2026-09-01 — Leverage (CONFIRMED, phase-dependent — NEW finding not in
## the original user-supplied rule summary)
| Asset class | Evaluation | Funded |
|---|---|---|
| Forex | 1:100 | 1:50 |
| Indices/Commodities | 1:20 | 1:10 |
| Crypto | 1:2 | 1:2 |

**Consequence:** available margin roughly halves the moment the account
transitions from Step 2 to funded. Any sizing engine ported into this
project MUST take leverage as a phase-dependent parameter, not a flat
constant per instrument (the pattern currently used in
`prop_firm_project/live/instrument_specs.py`'s `LEVERAGE_GFT`, which is
valid only for that project's 1-Step $10k account).

## 2026-09-01 — Goat Guard mechanics (CONFIRMED against primary source)
**Source:** `help.goatfundedtrader.com/en/articles/10742107-what-is-goat-guard`
(page timestamp: "1 week ago" as of pull date — more current than several
third-party review sites that describe this rule as an instant hard
breach; the official FAQ is treated as authoritative over those).

- Applies ONLY at funded stage (Step 3). Does not apply during Step 1/Step 2.
- Monitors COMBINED floating P&L across ALL open positions simultaneously
  — not per-instrument.
- Triggers at a combined floating loss = 2% of account balance.
- First trigger: SOFT breach. Positions stay open, account stays active,
  you're notified by email. Profit split is permanently cut to 50%
  (irreversible).
- Second trigger: HARD breach. Account closed permanently.
- GFT snapshots the position set at time of trigger; that exact position
  set fluctuating further below the 2% threshold does not re-trigger.

**Architecture consequence (the actual reason this is logged in detail):**
the existing Arm CC engine's risk controls (aggregate daily risk cap,
ATR-based stops) are all built on CLOSE-TO-CLOSE daily bars. Goat Guard is
a continuous, intrabar, mark-to-market check on the whole book. The
existing engine has no visibility into intrabar aggregate floating P&L at
all. This is a structural gap, not a parameter — see next entry.

## 2026-09-01 — Goat Guard modeling approach: INTRABAR (M15), decision made
## and reasoned, not a coin flip
Daily high/low was considered and rejected as the primary method. Rejection
reasoning: Goat Guard triggers on the SUM of floating P&L across
instruments at a single moment in time. Approximating this from each
instrument's independent daily high/low requires assuming all instruments'
worst intrabar excursions happened simultaneously — which is false by
construction (different instruments' daily extremes occur in different
sessions) and produces a large, one-directional OVERSTATEMENT of breach
frequency. That overstatement feeds directly into the ensemble-EV
denominator this whole project exists to estimate correctly.

M15 reconstructs actual time-stamped aggregate floating P&L paths and can
check the threshold at each 15-minute mark. Known limitation, stated
honestly: a spike-and-recover fully inside one 15-minute bar could trip
Goat Guard in live reality without appearing at M15 resolution — a small,
one-directional UNDERstatement, and preferable to the daily-HL method's
large, unquantified overstatement.

**If a daily-HL number is ever computed, it is reported as an explicit
worst-case upper-bound sensitivity check alongside the M15 primary
estimate — never as a replacement for it.**

**Consequence:** M15 history pull required for the candidate instrument
universe before Goat Guard can be simulated at all. See
`data/pull_m15_history.py`. Actual returned depth must be confirmed, not
assumed — same lesson as `prop_firm_project/research/addendum_03`, where
assumed 10-year D1 history turned out to be 7.8 years in practice on this
broker family.

## 2026-09-01 — Account cost: base-price search inconclusive; reframed as a sensitivity range, not a single number
Attempted to confirm a primary-source base price. Third-party sources
conflict with each other: FXEmpire cites "$36 starting," GFT's own blog
cites "$44," while PickMyTrade's FAQ is the only source that explicitly
distinguishes list vs. real-paid price -- $114-155 list across 2-Step
variants, with "the typical 20-50% promo running on GFT's site" bringing
real-paid price to $18-25. This last range is the only one consistent
with the user's originally-reported ~$18 figure.

**More important than any single number:** GFT's own homepage (pulled
earlier this session) displays "40% OFF + BOGO" and "50% OFF for New
Customers" simultaneously as default, non-limited-looking banners -- not
the signature of an occasional promotion, but of a discount structure
that may function as the de facto standing price. This was NOT
independently confirmed against an actual checkout flow (not attempted --
requires account purchase action, out of scope for a research pull).

**Decision: do not collapse this to one number.** Model as a two-point
sensitivity range in the ensemble-EV Monte Carlo, not a single point
estimate:
  - REALISTIC scenario: $18-25/rebuy (persistent-promo price -- the
    likely actual cost as long as GFT's current discount structure holds)
  - TAIL-RISK scenario: $114-155/rebuy (list price -- what a rebuy costs
    if that promo structure tightens or disappears mid-loop, a real
    possibility over a rebuy loop spanning months)

The gap between these two scenarios is large enough (roughly 6-8x) that
it plausibly changes the ensemble-EV conclusion on its own -- this needs
to be run as an explicit sensitivity check in P32, not glossed over by
picking the convenient number.

**Still open, and the fastest real resolution:** a screenshot of an
actual GFT checkout flow for the 2-Step GOAT $5k account, with any
auto-applied promo code visible, would resolve this definitively in
under a minute -- faster and more reliable than further third-party
searching. Recommended as the actual close-out step whenever convenient,
not urgent enough to block other work.

## 2026-09-01 — Account cost: CONFIRMED $36, direct checkout (supersedes prior sensitivity range)
User confirmed $36 for the $5k GOAT 2-Step account directly from an actual
checkout — this is a primary source (the real, current, personal price),
stronger than anything a third-party aggregator could provide, and closes
out the sensitivity-range placeholder logged just above.

**Worth noting plainly, not glossed over:** $36 doesn't cleanly match
either range estimated from third-party sources — it's above the
"realistic/persistent-promo" estimate ($18-25) and well below the
"tail-risk/list-price" estimate ($114-155). Plausible explanations: no
promo code was applied at checkout, the promo terms differ from what
aggregator sites reported, or pricing has simply moved since those pages
were last updated. None of this changes the decision — $36 from an actual
checkout is definitionally more reliable than any of the numbers that
produced the sensitivity range, so it replaces them outright rather than
being reconciled against them.

**Locked: `account.cost_usd = 36`.** This was the last blocker on account
economics for P32. Sensitivity-range fields removed from
`config/strategy.yaml` in favor of this single confirmed value.

## 2026-09-01 — Graduation trigger: 3 payouts (LOCKED)
**Decision:** capital migrates to the personal-capital objective after 3
successful funded-stage payouts. Locked now, before any simulation runs,
per the same discipline that prevents choosing a stopping point after
watching results.

**Explicitly flagged, not yet resolved:** reaching the graduation trigger
is a STRATEGY-OBJECTIVE change, not just a capital-size change. The
option-like, capped-downside reasoning that (conditionally, per the
literature reviewed) justifies running hotter than personal-capital-optimal
no longer applies once real capital is at stake. Migrating the SAME risk
parameters to personal capital at the graduation point would be a live
mistake. A follow-on hypothesis (P14/P15-style single-account survival-EV
remapping, run fresh against personal-capital risk tolerance) is required
at the graduation point — not written yet, flagged here so it isn't
forgotten when 3 payouts is actually reached.

## 2026-09-01 — Development location: local machine first, not VPS
**Decision:** all development and initial testing for this project happens
on the local machine, not `INSTANCE-202606`. VPS deployment is a later
step, once the hypothesis is locked and validated locally — mirroring the
local-dev / VPS-live split already used for both other projects (local
path convention: `D:\Quant research\MODELS\<project>`, VPS path:
`C:\<project>\live`), except here local is doing double duty as the
research/backtest environment AND the first live-signal test environment,
until this migrates to the VPS the way `prop_firm_project` and
`xau_xag_project` already have.

**Consequence for setup instructions:** the VPS's known Python constraint
(`virtualenv` required — `venv`/`ensurepip` absent on that GCP image) is
NOT assumed to apply to the local machine. Confirm local `pip`/`venv`
behavior directly rather than carrying over the VPS workaround by default.

**Consequence for paths:** all file paths in this project's scripts and
instructions should be given as local Windows paths going forward
(`D:\Quant research\MODELS\goat_5k_ensemble_project\...`), not VPS paths,
until a live-deployment step is explicitly reached.

## 2026-09-01 — M15 pull diagnosed and fixed: request-size cap, not a data/symbol problem
`pull_m15_history.py`'s original run returned NO_DATA for all 7 candidate
instruments. `diagnose_symbols.py` isolated the cause: same server
(GoatFunded-Server2, account 414222731), same `.x`-suffixed symbol names as
`prop_firm_project`'s account, `symbol_select` clean, `mt5.last_error()`
reporting Success throughout — and small requests (10, 1000 bars) worked
fine on both M15 and D1 for the tested symbol (USDJPY.x). The only
variable that changed between "works" and "fails" was request size: the
original script asked for 200,000 bars in a single `copy_rates_from_pos()`
call, which failed silently (no error code, just empty/None) — a known
MT5 API behavior where a per-call bar cap exists and is exceeded without
a clear error signal.

**Fix:** `pull_m15_history.py` rewritten to page backward in 5,000-bar
chunks (a size confirmed to work, not pushed to find the exact
undocumented ceiling — that boundary wasn't worth precisely identifying),
stopping when the server returns a partial chunk (signaling the start of
available history) or a 200-chunk safety ceiling is hit. Actual returned
depth per instrument still needs to be recorded here once the fixed
script is re-run — not assumed from this fix alone.

## 2026-09-01 — BTCUSD added to the candidate pull list (deliberate re-test)
`prop_firm_project`'s P12 research explicitly excluded BTCUSD from that
project's tradeable universe ("blocked at $5k / inconsistently tradeable
at $10k") for the Arm CC engine's survival-EV objective. Added here as a
deliberate re-test for this project's different objective (ensemble-EV)
and different account tier's specs — NOT an assumption that P12's finding
doesn't apply. If $5k GOAT-model contract specs (still blocked, see below)
confirm the same capacity problem, BTCUSD gets dropped from the universe
on that basis, consistent with P12, not re-litigated.

## 2026-09-01 — M15 pull SUCCEEDED after chunking fix; confirmed depth recorded
Every candidate instrument returned exactly 100,000 bars — not a
coincidence given the chunking safety ceiling was 1,000,000 (200 chunks x
5,000). This means the SERVER's M15 retention is a fixed bar count
(~100,000), not a fixed calendar window — the same category of surprise
already caught once for D1 in `prop_firm_project/addendum_03` (assumed
10yr, actual 7.8yr), now confirmed again at M15 on this broker.

Because a fixed bar count translates to different CALENDAR depth
depending on each instrument's trading-hours coverage (FX/WTI ~24/5,
BTCUSD ~24/7), depth is NOT uniform across the universe:

| Instrument | Earliest bar (UTC) | Latest bar | Calendar depth |
|---|---|---|---|
| USDJPY.x | 2022-08-04 21:15 | 2026-09-01 12:45 | ~4.1 yr |
| AUDCAD.x | 2022-08-04 19:15 | 2026-09-01 12:45 | ~4.1 yr |
| EURUSD.x | 2022-08-04 21:00 | 2026-09-01 12:45 | ~4.1 yr |
| USDCAD.x | 2022-08-04 21:00 | 2026-09-01 12:45 | ~4.1 yr |
| EURJPY.x | 2022-08-04 21:00 | 2026-09-01 12:45 | ~4.1 yr |
| GBPUSD.x | 2022-08-04 20:45 | 2026-09-01 12:45 | ~4.1 yr |
| WTI.x    | 2022-03-10 15:45 | 2026-09-01 12:45 | ~4.5 yr |
| BTCUSD.x | 2023-10-06 17:45 | 2026-09-01 12:45 | ~2.9 yr |

**Flagged honestly, not glossed over:** this is meaningfully thinner than
the ~7.8-year D1 window Arm CC was validated against in `prop_firm_project`.
BTCUSD's ~2.9 years is the thinnest of all — the one instrument being
deliberately re-tested for different (higher-payoff/convex) behavior has
the shallowest tail-risk evidence in the whole candidate set. This needs
to be stated as an explicit caveat in the P32 hypothesis file when locked,
not treated as equivalent-confidence to the FX/WTI legs. A block-bootstrap
Monte Carlo can still run against ~4 years of M15 data, but it will have
seen fewer distinct macro regimes than the D1-based work elsewhere in
this project family, and any result should be weighted accordingly.

`config/strategy.yaml`'s `data.history_pull_status` and
`data.m15_confirmed_depth` updated to reflect this.

## 2026-09-01 — $5k contract specs CONFIRMED — BTCUSD capacity concern flagged
Pulled directly from the live $5k GOAT account via `order_calc_margin()`
(real broker-computed margin at current price and account leverage, not
hand-derived from contract_size).

| Instrument | Min lot | Margin @ min lot | % of $5k balance |
|---|---|---|---|
| USDJPY.x | 0.01 | $10.00 | 0.2% |
| AUDCAD.x | 0.01 | $7.14 | 0.1% |
| EURUSD.x | 0.01 | $11.59 | 0.2% |
| USDCAD.x | 0.01 | $10.00 | 0.2% |
| EURJPY.x | 0.01 | $11.59 | 0.2% |
| GBPUSD.x | 0.01 | $13.54 | 0.3% |
| WTI.x | 0.01 | $4.41 | 0.1% |
| BTCUSD.x | 0.01 | **$389.72** | **7.9%** |

Six of seven instruments clear at negligible cost (0.1-0.3% of balance) —
no capacity concern, unlike the $10k universe in prop_firm_project where
several instruments rounded to zero. **BTCUSD is a real outlier**, roughly
30-80x every other instrument's min-lot margin footprint, driven by
crypto's 1:2 evaluation-phase leverage vs. 1:100 for forex (per the
leverage table already confirmed) — even the smallest tradable BTC
position ties up a disproportionate share of a $5k account.

**Consequence for sizing, not just feasibility:** cushion-proportional /
inverse-vol sizing computes a target weight and rounds to the nearest
tradable lot. If BTC's intended risk weight would normally be under 7.9%
of margin, the min-lot floor overrides the sizing model and forces an
oversized allocation — BTC's realized risk contribution would be set by
the exchange's minimum lot, not by the model. This is a genuine
design fork, not a disqualification:
  (a) exclude BTCUSD from the P32 universe, consistent with P12's original
      finding on a different (but related) capacity mechanism at $10k, or
  (b) include it with an explicit capacity-aware sizing rule (e.g. cap its
      target weight at what min-lot actually represents, or size it via a
      separate rule rather than the same continuous formula as the other
      six), tested deliberately, not silently glossed over.
**Not yet decided — flagged for explicit resolution before the hypothesis
locks.**

## 2026-09-01 — BTC decision: EXCLUDED (final)
Instrument universe locked at 7: USDJPY, AUDCAD, EURUSD, USDCAD, EURJPY,
GBPUSD, WTI. BTCUSD excluded on the capacity finding above — consistent
with, not contradicting, P12's original finding. `instrument_universe`
in `config/strategy.yaml` updated to `status: "CONFIRMED"`.

## 2026-09-01 — Graduation buffer: count-alone confirmed, no dollar floor added
Resolved by default rather than left open indefinitely: graduation
trigger is payout count alone (3, already locked), no additional
dollar-based confirmation. Adding one now would be complexity without
evidence it's needed — consistent with this project family's own finding
(six straight sophistication-over-simplicity failures in
prop_firm_project). Revisit only if the ensemble Monte Carlo itself shows
payout timing is erratic enough that count alone is a poor graduation
proxy — a concrete, evidence-based reason, not a precautionary add now.

## 2026-09-01 — D1 data reused from prop_firm_project, not re-pulled
The 7 confirmed instruments (USDJPY, AUDCAD, EURUSD, USDCAD, EURJPY,
GBPUSD, WTI) already have D1 history pulled and validated in
`prop_firm_project`. Price history is broker-side and not account-tier-
specific — the same GFT feed serves both the $10k 1-Step and $5k GOAT
accounts identically. Re-pulling would be redundant work producing
identical data. Decision: copy the existing D1 CSVs into this project's
`data/raw/` rather than re-run a pull against the new account.

## 2026-09-01 — D1 data source correction: not local disk, pulled from Claude Project reference files
The "D1 data reused from prop_firm_project" decision above assumed the
CSVs existed on local disk under prop_firm_project's folder — confirmed
WRONG by direct search (`Get-ChildItem -Recurse -Filter "*_D1.csv"`
returned nothing). The actual source was this Claude Project's uploaded
reference files, not a local prop_firm_project folder. Corrected: the 7
D1 CSVs (usdjpy, audcad, eurusd, usdcad, eurjpy, gbpusd, wti) were pulled
from there directly and handed to the user for manual placement in
data/raw/.

**Provenance gap, stated plainly:** this data's exact pull date, source
account, and MT5 session are not independently re-confirmed here — it's
whatever was uploaded to the Claude Project as reference material at some
earlier point, not a fresh, logged pull against either GFT account.
Coverage observed: 2018-06-28 to 2026-07-03 for the 6 FX pairs (2081
rows each), slightly shorter for WTI (2009 rows). Treat this as a
reasonable starting dataset to build the engine against, but if P32's
final results are ever reported as a locked conclusion, this provenance
gap should be closed with a real, dated, logged pull the same way the M15
data and contract specs were — not left as an inherited assumption.

## 2026-09-01 — Signal prep confirmed working against real data
`p32_signal_prep.py` run successfully. 6 FX pairs perfectly aligned:
2019-04-05 to 2026-07-03, 1881 valid signal+ATR bars each. WTI shorter/
later start (2019-07-02, 1809 valid bars) — expected, consistent with
its shorter raw D1 pull (2008 vs 2081 rows). No coverage mismatch
requiring resolution. Effective usable engine history: ~7.2 years
(FX), ~7.0 years (WTI) once the 200-day MA warmup is excluded.

## 2026-09-01 — p32_trend_engine.py built and smoke-tested against real data
Ran cleanly (no crashes) against the confirmed 7-instrument universe,
full available history (2019-07-02 to 2026-07-03, WTI-limited). Smoke
test at risk_per_trade=0.8%, phase='evaluation', no aggregate daily risk
cap, $5,000 starting equity:
  - Total return: 244.90%
  - Annualized Sharpe (log returns): 1.978
  - Max drawdown: -11.03%

**Real finding, not just a pipeline check:** max drawdown (-11.03%)
EXCEEDS GOAT's confirmed 10% static max loss. At this risk level, with no
account-level governance, this portfolio would have breached the account
at least once across the tested history. This is the concrete evidence
for why p32_multistage_sim.py's governance layer (daily 4% / static 10%
halts, Step1->Step2->funded transitions) is not optional scaffolding —
the trend engine alone, unconstrained, is not survivable at 0.8% risk
over this history. Whether a lower risk level keeps the trend engine's
apparent edge while staying inside the drawdown envelope is exactly the
kind of question the eventual ensemble-EV base-risk mapping needs to
answer — not assumed from this one smoke-test point.

**Two honest simplifications baked into this engine, not yet closed out:**
cost model is flat spread-based (addendum_05's vol-scaled model was
prose-only, no source code available to port), and POINT_SIZE per
instrument is an assumed standard MT5 convention, not independently
pulled from symbol_info().point. Both flagged in the file's own
docstring. Sharpe/return figures above should be treated as directionally
useful for pipeline testing, not as validated final numbers, until these
are closed out.

## 2026-09-01 — p32_multistage_sim.py built and smoke-tested against real data
State machine (Step1 -> Step2 -> Funded, daily/static governance,
valid-day and payout-cycle tracking) ran cleanly. Smoke test at
risk_per_trade=0.8% (Arm CC's inherited calibration), starting from the
earliest common date (2019-07-02):

  - Step 1: BREACH_STATIC at 2020-02-28, equity $4,468.22 (below the
    $4,500 static floor), after 172 days and 7 valid trading days.

**Real finding, not a bug:** the breach date coincides with the COVID
market crash — an explicable macro event, not an artifact of the
governance logic. This is direct, concrete confirmation of what P31/P32's
whole premise anticipated: 0.8% risk, calibrated for a completely
different account (prop_firm_project's $10k 1-Step, different DD limits,
different universe-capacity profile), is NOT assumed to survive on this
account structure — and on this specific historical path, tested
rigorously with real governance, it doesn't. This is exactly the kind of
result the eventual ensemble-EV base-risk mapping needs to characterize
properly (across many bootstrapped paths, not one), not a result to
react to by hand-picking a lower risk level now.

**Still placeholder, still flagged:** Goat Guard (funded-stage combined
floating-loss check) remains unimplemented — this smoke test never
reached the funded stage to exercise that gap anyway, but it's not
forgotten. Also carried forward: the payout-cycle-boundary interpretation
(evaluate eligibility at the end of each fixed 14-day window, not
continuously) is a reasonable reading of the FAQ, not independently
confirmed against GFT support.

## 2026-09-01 — Risk grid: keeping P31's original 0.5%-1.0%, not reacting to n=1
The Step 1 breach at 0.8% (single path, 2020-02-28) is one historical
draw, not evidence 0.8% is broadly unviable — exactly why the ensemble
Monte Carlo needs to run across many bootstrapped starting points rather
than judging a risk level from one path. Keeping P31's original grid
(0.5%, 0.6%, 0.7%, 0.8%, 0.9%, 1.0%) as the default for ensemble_ev.py
unless the full Monte Carlo itself shows the grid needs to shift —
evidence-based, not reactive to a single anecdote.

## 2026-09-01 — FLAGGED, NOT YET DECIDED: constant-risk vs. cushion-proportional sizing
p32_trend_engine.py deliberately uses constant risk_per_trade (documented
in its own docstring as NOT including Arm CC's cushion-proportional,
drawdown-responsive dynamic sizing). This is a real architecture fork,
not a minor detail: the ensemble Monte Carlo built next will test a
DIFFERENT sizing mechanism than the one actually validated in
prop_firm_project.

**In favor of testing constant-risk first, as currently built:** P14/P15
already found the cushion mechanism itself was NOT the main EV driver at
$10k — base risk level was. Constant-risk is a legitimate, simpler
baseline to test given that finding, not an obviously wrong simplification.

**Against:** the Step 1 smoke-test breach shows constant-risk sizing does
NOT self-throttle as aggressively as Arm CC's cushion mechanism would
near a drawdown limit — a cushion-style size reduction as equity
approaches the 10% static floor might have avoided or delayed that
specific breach.

**Not yet decided.** Recommendation: run ensemble_ev.py first against
constant-risk (cheaper, already built, tests a real hypothesis given the
P14/P15 precedent). If results are poor or inconclusive across the full
Monte Carlo, cushion-proportional sizing (re-calibrated D_max/floor for
GOAT's 10% static limit, not copied from Arm CC's 8%/0.15) becomes the
natural next hypothesis to test — not added preemptively without
evidence it's needed.

## 2026-09-01 — Goat Guard wired into p32_multistage_sim.py (real, not placeholder)
Three files changed together:

1. **p32_trend_engine.py**: added `notional_usd` to closed-trade log
   entries, and fixed a real gap — positions still open at a segment's
   end were previously never logged at all (silently missing from
   Goat Guard's exposure reconstruction). Both changes verified purely
   additive: re-ran the original smoke test, identical output (244.90%
   total return, Sharpe 1.978, -11.03% max DD) before and after.

2. **p32_multistage_sim.py**: `_run_segment()` now captures the real
   trade log for funded-stage (payout-cycle) segments and calls
   `goat_guard_m15.check_positions_against_m15()` at each 14-day cycle
   boundary — not a placeholder anymore. Strikes tracked CUMULATIVELY
   across the whole funded stage (matches GFT's real rule: 1st trigger
   = soft, 2nd = hard breach, not reset per cycle). M15 data loaded
   once per lifecycle, not once per cycle. New outcome:
   `BREACH_GOAT_GUARD`.

3. **goat_guard_wiring_test.py** (new): isolated test of the wiring
   logic using synthetic data, since the existing full-lifecycle smoke
   test breaches at Step 1 and never reaches the funded stage — meaning
   it CANNOT exercise this new code at all. Both wiring tests pass
   (threshold-crossing detection, and correct handling of a
   still-open-at-segment-end position specifically).

**Two honest limitations, carried forward from goat_guard_m15.py, not
resolved by this wiring:**
  - Goat Guard's account_balance check uses a FIXED reference (the
    cycle's starting equity), not GFT's real continuously-evolving
    balance.
  - STILL NOT TESTED AGAINST REAL M15 DATA — only synthetic. The actual
    test is running a real funded-stage path (once one survives that
    far) on your machine and confirming it behaves sensibly, not this
    isolated logic test.

## 2026-09-01 — All three wired files confirmed matching on user's machine
File-delivery mismatch from earlier fully resolved (one file had silently
saved as `goat_wiring_test.py` instead of `goat_guard_wiring_test.py` —
renamed, then all three ran cleanly). p32_trend_engine.py and
p32_multistage_sim.py both reproduce identical smoke-test numbers to
before the wiring changes (244.90%/1.978/-11.03%, and the same Step 1
breach at 2020-02-28), confirming the additions were purely additive as
intended. goat_guard_wiring_test.py's both synthetic tests pass on the
user's machine, matching what was verified locally. Goat Guard wiring is
now confirmed working end-to-end on synthetic data, on both machines —
only real M15 data compatibility remains unverified (no path tested so
far has survived to the funded stage to exercise that).

## 2026-09-01 — Two quant calls locked: constant-risk sizing, real calendar-anchored sweep
1. **Sizing:** constant-risk confirmed as the build basis (already
   flagged, now locked) — P14/P15 precedent (cushion mechanism wasn't
   the main EV driver at $10k) is real justification for testing the
   simpler mechanism first.
2. **Monte Carlo methodology:** real, calendar-anchored starting-point
   sweep, NOT statistical block-bootstrap (P31's original convention).
   Reasoning: block-bootstrap produces synthetic dates with no real M15
   data to check Goat Guard against — approximating that would throw
   away the exact reconstruction just built and tested. Tradeoff:
   ~100+ real starting points instead of P31's 5,000 bootstrapped paths
   — fewer paths with genuine answers over more paths with a fabricated
   Goat Guard signal.

## 2026-09-01 — Rebuy-chain function built; real performance fix; real bug caught and fixed pre-handoff
Added `run_lifecycle_chain()` to p32_multistage_sim.py — the actual
rebuy loop: repeated fresh $5,000 attempts (each costing the confirmed
$36), continuing chronologically forward through real dates on breach,
until graduation, a wall-clock budget, or data exhaustion.

**Performance fix, needed before any sweep is viable:** `_run_segment`
previously re-ran the ENTIRE historical simulation from scratch on every
call. Added `_get_cached_simulation()` — caches the full historical
return series per (phase, log_trades), computed once and reused across
segments/attempts within the same risk level. FLAGGED HONEST
APPROXIMATION: cache is computed at a fixed $5,000 reference equity;
real segments start at whatever equity the prior segment ended at.
Fractional returns are mostly equity-scale-invariant in this engine
except for MIN_LOT rounding effects, so expected distortion is small
given typical notional-per-lot sizes relative to this account's equity
range — but not exactly zero, not precisely quantified.

**Real bug caught before handoff, not shipped:** initial version of
`run_lifecycle_chain` eagerly loaded M15 data upfront even for chains
where every attempt might breach during evaluation and never need it —
meaning it would fail on missing M15 data even when genuinely
irrelevant. Fixed to lazy-load (only when a funded segment is actually
reached), with the cache mutated in place so it's shared correctly
across chained attempts, not reloaded per attempt.

**Local test result (chain, risk=0.8%, starting from earliest common
date, 3-year wall-clock budget):** attempt #1 breaches exactly as
before (BREACH_STATIC, 2020-02-28). Attempt #2 — rebuying the very next
day — PASSES both Step 1 and Step 2 and reaches the funded stage. This
is genuine, real information: fast recovery and re-attempt worked on
this specific historical window. Could not verify further locally — no
real M15 data available in this environment. This is exactly the point
where the real test needs to happen on the user's machine, against
actual M15 data.

## 2026-09-01 — MILESTONE: first end-to-end confirmation against real data
Full pipeline test, risk=0.8%, starting from earliest common date
(2019-07-02), 3-year wall-clock budget, run on the user's machine
against real M15 data for the first time:

  - Attempt 1: FAILED_STEP1_BREACH_STATIC at 2020-02-28 (matches every
    prior test exactly)
  - Attempt 2 (rebuy, starting 2020-02-29): PASSED Step 1, PASSED
    Step 2, survived the funded stage's real M15-checked Goat Guard
    exposure across multiple payout cycles, GRADUATED at 3 payouts by
    2021-02-16. Ending equity $6,400.21.
  - Total cost: $72.00 (2 attempts x confirmed $36)

**This is the first real confirmation that every component built in this
project works together correctly on genuine data** — signal engine,
governance (daily/static DD, valid-day counting), payout-cycle tracking,
the rebuy chain, and specifically the M15-based Goat Guard reconstruction
(which had only been verified against synthetic data until this run).
No BREACH_GOAT_GUARD occurred on this path, meaning Goat Guard was
genuinely checked against real intrabar data through the funded stage
and did not accumulate 2 cumulative strikes.

**Single path, not a conclusion.** This is one starting point, one risk
level — informative and genuinely encouraging, but not yet evidence
about risk=0.8% broadly, any more than the earlier single-path breach
was evidence 0.8% was broadly unviable. This is exactly what the full
ensemble sweep (many real starting points x the full risk grid) exists
to characterize properly.

## 2026-09-01 — ensemble_ev.py built: the actual full sweep
Real risk grid (0.5%-1.0%) x real calendar-anchored starting points
(spaced 21 days apart across the full common-coverage history), each
running the full rebuy chain over a 3-year wall-clock budget. Computes
the actual P31/P32 objective — net payout $ per $1 of account cost — not
just pass/fail counts.

**Real dollar payout accounting added** (compute_payout_dollar_amount):
80% profit split, capped at 4% of the $5,000 initial balance for the
first 2 payouts (confirmed) and — UNCONFIRMED, flagged — the same 4%
cap conservatively assumed for payout 3+, since the FAQ only specified
the cap for "the initial 2 payouts." Assuming a lower/equal cap rather
than an optimistic uncapped one is the safer direction to be wrong in.
5 isolated unit tests of this function (under-cap, at-cap for payouts
1/2/3, and a loss producing zero payout) all pass.

**Real, unaddressed gap, flagged not hidden:** payout withdrawals are
computed but NOT deducted from the simulated equity path — equity
continues compounding as if the payout were never withdrawn. Makes the
account look slightly more resilient post-payout than it truly would be.
Needs fixing before results are treated as final.

**Real bug caught and fixed during testing, not shipped:**
p32_multistage_sim.py only ever imported pandas inside its own __main__
block — invisible every time that file was run directly, but broke with
a bare NameError the moment ensemble_ev.py imported run_lifecycle_chain
as a library function without triggering that block. Moved the import
to module level. Caught by exactly the kind of test built to catch it
(a reduced-scope local sweep run before handoff), not discovered by the
user.

**Local verification, as far as it goes without real M15 data:** ran a
reduced-scope sweep (13 starting points, 1 risk level, 1-year budget)
locally. All 13 correctly reached the funded stage and errored ONLY on
the expected missing-M15-data issue — confirming the caching, the
per-path error handling (no single failure crashes the sweep), and
performance (13 paths in ~1 second) all work correctly. The actual EV
numbers require real M15 data and must be run on the user's machine —
this is the real test still pending, same as the wiring before it.

## 2026-09-01 — Speed & cap-binding diagnostics added to ensemble_ev.py
Direct response to the first full sweep result: 100% graduation and
statistically indistinguishable EV/$ across the ENTIRE 0.5%-1.0% risk
grid — meaning the sweep as first built could not actually differentiate
risk levels. Added a second summary table: mean/std attempts-to-graduate,
mean/std calendar-days-to-graduate, and payout cap-binding rate (fraction
of payouts where the confirmed $200 cap, not actual cycle profit,
determined the payout amount).

**Hypothesis being tested by this addition:** the $200 hard payout cap
(4% of $5,000) means max possible value per path is capped at $600
regardless of risk level — higher risk could only reach that ceiling
FASTER, never exceed it, which would fully explain flat EV/$ while still
leaving speed-to-graduate as a real differentiator. Cap-bound % directly
confirms or kills this mechanism; attempts/days-to-graduate is where the
actual risk-level tradeoff would show up if EV/$ can't show it.

Real bug caught during testing, not shipped: a leftover duplicate
"N (err)" print line ended up at the bottom of the file after the new
table was inserted — caught and removed before handoff.

Verified via isolated unit test against a fabricated chain result (not
real data, since real graduated paths require M15 data unavailable in
this environment): payout total, cap-bound count, and days-to-graduate
all compute correctly against hand-checked expected values. Both summary
tables render correctly. Real numbers still require a re-run on the
user's machine — this only confirms the NEW logic is correct, not what
the actual cap-binding rate or speed differential turns out to be.

## 2026-09-01 — EV-per-day metric added, correcting a real flaw in the original objective
Direct response to the second sweep's own data: EV/$ treats a path
graduating in 315 days identically to one taking 886 days, as long as
the dollar total matches — but days-to-graduate varied nearly 3x across
the risk grid while EV/$ barely moved. That's not a neutral simplification,
it's a wrong economic assumption baked into the headline metric. Added
`ev_per_day` = net_ev / total_elapsed_days, where total_elapsed_days is
the ACTUAL days-to-graduate for graduated paths, or the FULL wall-clock
budget for paths that never graduated (capital was tied up the entire
budget period regardless of partial progress — the economically correct
basis, not a convenience shortcut).

Verified via two hand-checked unit tests (graduated case: net_ev=$444
over 244 days = 1.8197/day; budget-exhausted case: net_ev=$44 over the
full 1095-day budget = 0.0402/day) plus a mixed-scenario summarize()
render — both pass exactly against manually computed expected values.

EV/$ is KEPT in the output, not replaced — for continuity with the
original P31/P32 objective and because the gap between what EV/$ and
EV/day each show is itself diagnostic. EV/day is now stated explicitly
in the printed output as the metric that should actually drive a
risk-level decision.

Real numbers from this addition still require a re-run on the user's
machine — same pattern as every diagnostic added so far.

## 2026-09-02 — Risk grid extended: 0.5%-1.0% never bracketed an optimum
Second full sweep (with EV/day) showed the metric climbing monotonically
across the ENTIRE original grid (0.412/day at 0.5% -> 1.516/day at
1.0%), no reversal anywhere — meaning the tested range only found its own
ceiling, not the strategy's. Extended to
[0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.4, 1.6, 2.0]% — fixed now,
before running, same discipline as the original grid.

**Self-correction on record:** the previous session's recommendation
(0.8-0.9%) leaned on an analogy to prop_firm_project's P15 finding (a
capacity wall just above 1.0%, from that project's 3% aggregate daily
risk cap binding). That mechanism does NOT apply here — this engine has
never had aggregate_daily_risk_cap set (default None throughout). Reached
for a plausible-sounding pattern from a different project without
checking whether the actual mechanism transfers. Flagged and corrected
rather than left standing.

**Real mechanism expected to eventually flatten/reverse EV/day in this
engine specifically:** position sizing is capped by available margin
(`lots_raw = min(lots_raw, equity / margin_per_lot)` in
p32_trend_engine.py). At high enough risk, the risk-based lot
calculation exceeds what margin allows — risk_per_trade becomes partly
fictional beyond that point, since the position can't actually grow to
match the intended risk. Whether this shows up within the extended
range, or further out, is what the next run will tell — stated as a
hypothesis now, before results exist, not fit to whatever the extended
sweep happens to show.

## 2026-09-02 — Two real bugs fixed together: payout deduction + boundary-day double-counting
Fixing the flagged payout-deduction gap required touching the same
equity-carry logic used at every segment transition, which surfaced a
SECOND, more significant bug in the process — both fixed together since
they're the same mechanism.

**Bug 1 (the one asked for): payout withdrawals weren't deducted from
simulated equity.** Every subsequent daily/static/Goat-Guard check keyed
off an artificially inflated post-payout balance. Fixed:
`equity = ending_equity - payout_amount` at the point of withdrawal, in
`run_full_lifecycle`. `payout_amount` is now computed ONCE, at
withdrawal time, and stored on the payout event
(`payouts[-1]["payout_amount"]`) — `ensemble_ev.py` reads this directly
rather than recomputing it via `compute_payout_dollar_amount()` a second
time, removing any risk of the two calculations drifting apart.

**Bug 2 (discovered while fixing Bug 1, not asked for but couldn't be
left in place once seen): every segment transition — Step1->Step2,
Step2->Funded, and every ~14-day funded cycle boundary — passed the
SAME date as both the end of one segment and the start of the next.**
`_run_segment` slices with `full_returns.index >= start_date` (inclusive),
so that boundary day's return was applied TWICE: once as the terminal
day of the old segment, again as day 1 of the new one. Given funded
cycles recur roughly every 14 days over paths spanning hundreds of days,
this could have compounded incorrectly dozens of times per lifecycle.
Fixed by advancing `current_date` by +1 day at every transition, matching
the pattern already correctly used in `run_lifecycle_chain`'s between-
attempt rebuy logic. Also fixed a related precision issue this same
change surfaced: the `GRADUATED` return previously used the advanced
(+1 day) `current_date` as its reported `end_date`, off by one day from
the actual last payout — corrected to use `payouts[-1]["date"]` directly.

**Verified with controlled, deterministic tests** (not real data, since
real M15-dependent funded-stage paths aren't testable in this
environment): a synthetic return series with a distinctive +5% day
immediately after a Step 1 pass confirms the fixed version correctly
advances to that day ($5,463.17), while reproducing the pre-fix behavior
shows it would have re-applied the already-counted prior day instead
($5,255.05) — a real, meaningfully different result, not a rounding
artifact. A separate direct test confirms the payout deduction math
itself (a $1,000 cycle profit produces a $200 capped payout, correctly
reducing carried-forward equity from $6,000 to $5,800, not leaving it
at $6,000).

**Consequence for every prior sweep result:** both bugs affected the
FULL sweeps already run and reported (the 6-level and 11-level grids).
Those results are NOT reliable as-is — Bug 2 in particular could have
distorted equity trajectories throughout the funded stage in ways that
are hard to sign a priori (sometimes inflating, sometimes deflating,
depending on whether the re-counted day was positive or negative).
**Every previously reported EV/day, graduation rate, and days-to-graduate
number needs to be treated as superseded, not as a baseline to compare
against.** The next full sweep is the first one run against a materially
corrected engine.

## 2026-09-02 — Focused resweep configured: 1.0%-1.6% band, finer grid, denser starts
Direct response to the corrected sweep's noisy 1.0-1.6% region (EV/day
dipped at 1.1%, recovered, peaked ~1.6%; graduation rate specifically
dipped to 82.3% at 1.6% before recovering to 100% at 2.0% — a wobble,
not a clean pattern). Reconfigured `ensemble_ev.py` for a focused zoom:
13 risk levels at 0.05% steps (1.00% through 1.60%), 10-day starting-
point spacing (roughly 2x the point count vs. the prior 21-day spacing).
Does NOT re-test 0.5-0.9% or 2.0% — those stand from the corrected
coarse sweep, not superseded here.

**Statistical honesty, stated in both code comments and the printed
output itself (not just logged here):** starting points 10 days apart
are NOT independent draws — each shares nearly its entire multi-year
window with its neighbors. Denser spacing adds real but limited
additional power, not power proportional to the ~2x point-count
increase. Flagged explicitly so a tighter reported std isn't misread as
a proportionally more powerful independent result.

Also caught and fixed two stale references while making this change:
the file's closing summary message still described payout withdrawals
as "not yet deducted" — corrected, since that was fixed earlier this
session and this run reflects the correction.

## 2026-09-02 — Two fixes: display precision bug + multiprocessing for runtime
**Bug found in the just-received focused-resweep output:** the risk
column printed with only 1 decimal place (`.1%`), making 1.05%, 1.10%,
and 1.15% all display as "1.1%" — the reported table showed apparent
duplicate rows that were actually distinct risk levels displayed
ambiguously. Fixed to 2 decimals (`.2%`) across both summary tables.
The underlying 13-level sweep data was very likely correct; only the
display couldn't distinguish it.

**Runtime workaround, in direct response to "is there no other
workaround":** parallelized `run_sweep()` across CPU cores using
`multiprocessing.Pool` — each risk level is fully independent of every
other (nothing about testing 1.1% depends on 1.0% already having run),
so risk levels now run SIMULTANEOUSLY instead of sequentially. Real
speedup, not a rigor tradeoff — same computation, concurrent instead of
serial. Defaults to `cpu_count() - 1` workers.

Real cost: fine-grained "10/256 done" progress within a risk level is
lost — each worker only prints once, when its entire risk level
finishes. Traded deliberately for the much larger wall-clock win;
flagged in the function's own docstring as a real, not free, tradeoff.

Verified with a reduced-scope structural test (4 risk levels, n_workers=4):
correct result reassembly, correct RISK_GRID ordering preserved despite
`imap_unordered`, correct per-path error handling still intact across
process boundaries (all 9 expected missing-M15-data errors recorded
correctly, nothing crashed or hung). This sandbox reports only 1 CPU
core, so no real timing speedup was observable here — that verification
can only happen on the user's actual multi-core machine.

## 2026-09-02 — CRITICAL FIX: transaction costs were zero this entire time, not "simplified"
Traced a real, severe bug: `p32_signal_prep.py`'s `load_d1()` dropped the
`spread` column at the very first data-loading step. Every downstream
DataFrame never carried it. `p32_trend_engine.py`'s `_spread_cost_frac()`
has always had a defensive `if "spread" not in df.columns: return 0.0`
check — which fired on EVERY call, across ALL THREE ensemble sweeps run
so far. Every simulated trade paid zero transaction cost. The engine's
own docstring described this as a "simplified flat cost model," which
was itself wrong — there was no flat cost being applied at all.

**Fixed in the same pass, upgraded rather than just reconnected:**
`load_d1()` and `prep_instrument()` now carry `spread` through, and a
real vol-scaled cost multiplier (`compute_vol_multiplier_series`,
reimplementing addendum_05's described methodology fresh, since the
original `cost_model.py` source was never available to port verbatim)
scales the recorded spread up during high-realized-volatility periods,
floored at 1.0 (never scales down), matching addendum_05's stated
principle that recorded spread is a noisy floor, not a ceiling.

**Sanity-checked before trusting it, not just accepted:** vol multiplier
means run 1.13-1.32 across instruments (medians exactly 1.0 by
construction), no signs of the multiplier exploding or misbehaving.
Round-trip cost per trade for USDJPY sampled at ~0.25-0.33% — realistic,
not a bug producing absurd costs.

**Real result of the fix — smoke test, risk=0.8%, same path tested
throughout this project:**

| | Zero-cost (all prior sweeps) | Real cost (fixed) |
|---|---|---|
| Total return | 244.90% | 17.24% |
| Sharpe | 1.978 | 0.258 |
| Max drawdown | -11.03% | -24.77% |

**Root cause of the magnitude, confirmed not a bug:** ~15.8 trades/year
per instrument (~800 total across the portfolio, 7.2 years) — far more
frequent than a clean trend-follower typically shows, consistent with
the 2xATR Chandelier stop producing real whipsaw during choppy periods
(29.4% win rate — cut losses fast, let rare trends run, a genuine
trend-following signature). At this frequency, even modest per-trade
costs compound into a large total drag — exactly what happened once
costs were finally turned on.

**Consequence, stated plainly: every ensemble sweep result reported so
far (all three, including the 1.20-1.30% recommendation) ran on a
cost-free engine and must be treated as superseded, not as directionally
correct with a footnote.** A full re-sweep against the corrected engine
is required before any risk-level number is trustworthy again.

## 2026-09-02 — Cushion-proportional sizing built at core engine level; NOT yet safe for the multistage/ensemble pipeline
Added `sizing_mode` parameter to `simulate_portfolio()`: 'constant'
(default, unchanged original behavior) or 'cushion' (Arm CC-style
dynamic sizing: `size_mult = clip(1 - drawdown_from_HWM/D_MAX, FLOOR, 1.0)`
applied to risk_per_trade at every new entry). D_MAX=0.08 and
FLOOR=0.15 chosen fresh for THIS account's confirmed 10% static floor
(2-point safety margin) and the already-learned P17 permanent-halt-trap
lesson, respectively — same numeric values as prop_firm_project's Arm CC
by independent reasoning, not copied blindly (Arm CC's D_max was tied to
a DIFFERENT account's 6% static floor via a separate governor layer this
project doesn't have).

**Real design conflict discovered mid-implementation, not silently
shipped around:** cushion sizing needs a HWM that persists continuously
across an entire account lifecycle (Step1->Step2->every funded cycle),
resetting only on a genuine fresh rebuy. The existing architecture calls
`simulate_portfolio()` fresh per SEGMENT, and the performance cache
(`_get_cached_simulation`) computes one full-history run once and slices
it per starting date — correct for constant-risk sizing (fully local,
day's position size only depends on that day's own equity) but WRONG for
cushion sizing under caching (the cached run's HWM reflects the entire
shared history's peak, not each attempt's own local HWM) and ALSO wrong
under per-segment fresh calls without the cache (HWM would incorrectly
reset at every 14-day funded cycle boundary).

**Decision: stop here rather than ship it broken.** Cushion sizing is
built and verified CORRECT at the standalone `simulate_portfolio()`
level only. It is NOT wired into `p32_multistage_sim.py` or
`ensemble_ev.py`, and must not be enabled there until the HWM-persistence
and cache-incompatibility issues are actually resolved — using
`sizing_mode='cushion'` through the existing multistage pipeline today
would silently compute wrong numbers, not just imprecise ones.

**Standalone verification, risk=1.2%, same path as other core tests:**

| Mode | Total return | Sharpe | Max DD |
|---|---|---|---|
| constant | 21.43% | 0.202 | -30.19% |
| cushion | -8.67% | -0.466 | -10.33% |

Mechanism confirmed directionally correct — drawdown protection is real
and substantial (-30% -> -10%). But the cost is also real and
substantial: total return flips from positive to meaningfully negative
at this risk level. Not treated as "cushion sizing is better" or "worse"
on this basis alone — one path, one risk level, and the real multistage/
ensemble comparison (the only test that would actually answer "is
cushion better") is exactly what's blocked pending the fix above.

**Way forward:** the HWM-persistence fix requires threading an external,
evolving HWM state through `_run_segment`/`run_full_lifecycle` (each
segment call needs to receive and return the accumulated HWM, not
reset it), and either disabling the sim_cache for cushion mode entirely
(accepting the performance cost) or redesigning the cache to key on
something compatible with path-dependent sizing. Real, scoped work —
not started yet, correctly flagged as the next concrete task rather than
rushed into the same response as everything else fixed today.

## 2026-09-02 — Payout-3+ cap: closed, not further pursued
Per direct instruction: the real objective is reaching at least 3
payouts (graduation itself), not extracting maximum precision from an
uncertain payout-3+ cap value. Closing this open item — the existing
conservative assumption (PAYOUT_CAP_PCT_LATER = same 4% as payouts 1-2)
stands as the working assumption, no further GFT confirmation being
pursued. If this reading of the instruction is wrong, flag it and this
gets reopened.

## 2026-09-02 — Cost model fix CONFIRMED matching on user's machine
Earlier discrepancy (user's first post-fix run showed 35.70%/0.498/-17.62%
vs. my 17.24%/0.258/-24.77%) resolved: stale `__pycache__` bytecode from
before the fix, not a save or logic problem. Confirmed via isolated
vol-multiplier check (mean 1.253, median 1.0001, max 3.481 — matching my
numbers almost exactly) that inputs were identical, then a clean re-run
after `__pycache__` removal reproduced my exact smoke-test numbers
(17.24%/0.258/-24.77%). Cost model fix is now genuinely verified correct
on both machines, not just claimed.

## 2026-09-02 — Risk grid reset: wide coarse sweep, superseding 1.0%-1.6% region assumption
Given the confirmed magnitude of the cost-model fix (Sharpe 1.978->0.258
at just 0.8% risk in the standalone smoke test), the previously-assumed
"region of interest" (1.0%-1.6%, found under a completely cost-free
engine) can no longer be trusted to still be where the real optimum
sits. Reset `ensemble_ev.py`'s RISK_GRID to a wide coarse sweep
[0.3%, 0.5%, 0.7%, 1.0%, 1.3%, 1.6%, 2.0%, 2.5%] and starting-point
spacing back to 21 days (from the focused resweep's 10 days) —
deliberately mirroring the ORIGINAL two-stage approach (wide cheap sweep
first, focused resweep once the region is known) rather than assuming
the old focus point still applies. Grid extends lower than anything
tested before (0.3%) since real costs may now favor meaningfully more
conservative sizing than the cost-free analysis suggested.

## 2026-09-02 — Wide coarse sweep result (cost-corrected, first real one) + focused resweep configured
**Wide sweep result:** conclusive on the low end — 0.30% never completed
a single full-budget path across 122 starting points (real data
exhaustion every time, not close calls), 0.50% took a mean of 1,870 days
(>5 years) to graduate on the 23 paths that did complete. Both too slow
to be viable under real costs. Efficiency (mean/std of EV/day) declined
steadily from 0.50% (2.40) through 1.60% (0.97) — same pattern as every
honest sweep so far. 2.50% showed a real, unambiguous wall: 71.6%
graduation, mean 10.17 rebuy attempts, 76.8% cap-bound rate (capped
upside + uncapped cost — a genuinely bad regime, not noise).

**Not yet resolved:** 2.00% spiked to nearly double every neighboring
level's EV/day (1.781 vs 1.60%'s 0.943 and 2.50%'s 1.014), sitting
between two visibly worse points. Flagged as needing the same scrutiny
the earlier "1.6% peak" got before it turned out to be sampling noise
under denser resolution — not trusted on 21-day/coarse-grid evidence
alone.

**Reconfigured `ensemble_ev.py` for a focused resweep:** 1.60%-2.20% at
0.05% steps (13 levels), 10-day starting-point spacing — same convention
as the earlier successful resweep that resolved the previous noisy
region. Does not re-test outside this band; the wide sweep's low-end
conclusion stands.

## 2026-09-02 — Resweep result: 2.00% peak CONFIRMED real, but a real bias risk found in how it's measured
**Confirmed:** the coarse sweep's 2.00% spike is genuine local structure,
not sampling noise — EV/day at 1.95-2.00% (1.83, 1.85) sits far above
both neighbors (1.85%: 0.73, 2.05%: 1.48) across multiple adjacent
0.05%-spaced points, unlike the earlier "1.6% peak" that dissolved under
denser sampling. Answers the question this resweep was built to answer.

**But:** N(trunc) jumps from 18 (1.90%) to 111 (1.95%) — nearly 50% of
the sample excluded, at exactly the point EV/day spikes. Real concern:
truncated (DATA_EXHAUSTED) paths were being silently EXCLUDED from
headline stats, and if truncation disproportionately removes slower/
worse-performing paths at some risk levels more than others, the
reported mean becomes survivorship-biased in a way correlated with risk
level itself.

**Two real bugs fixed in the same pass, not just a caveat added:**
1. `evaluate_chain_result()` was using the FULL nominal wall-clock
   budget (1095 days) as the time base for EVERY non-graduated path,
   including DATA_EXHAUSTED ones — wrong specifically for that case,
   since a path cut short by real data exhaustion never consumed the
   full budget. Fixed to use actual elapsed time for DATA_EXHAUSTED,
   keeping the full budget only for genuine BUDGET_EXHAUSTED paths.
2. `summarize()` no longer silently drops truncated paths. New
   "SENSITIVITY CHECK" table reports EV/day both excluding and
   including truncated paths side by side, with a Delta column and an
   explicit flag for any risk level where the gap exceeds 0.3 — makes
   the bias question visible and checkable per risk level, not hidden
   behind a single number.

**Verified with exact-value unit tests** (fabricated chain results, not
real data): DATA_EXHAUSTED case correctly computes 213 real elapsed days
(not 1095), matching a hand-calculated ev_per_day exactly. BUDGET_EXHAUSTED
still correctly uses the full budget, unchanged. Sensitivity table
correctly detects and surfaces a deliberately-fabricated bias pattern
(Delta growing monotonically with %truncated) in a structural test.

**Not yet known:** whether this bias is actually present and large
enough to explain the 2.00% peak's magnitude in the REAL data — that's
exactly what re-running the resweep with this fix will show. The peak's
existence as real structure is confirmed; its true size is not yet.

## 2026-09-02 — Cushion sizing HWM-persistence fix built and tested; unresolved numerical discrepancy investigated and logged honestly
Built the actual fix for the HWM-persistence problem flagged when cushion
sizing was first added: `simulate_portfolio()` now accepts `external_hwm`
(carries a prior segment's high-water-mark forward instead of resetting
to starting_equity on every call) and `return_final_hwm` (so the caller
can retrieve it). `_run_segment()`, `run_full_lifecycle()`, and
`run_lifecycle_chain()` all thread `sizing_mode` and the evolving
`current_hwm` through correctly — HWM now persists across Step1->Step2->
every funded cycle within one lifecycle attempt, resetting only on a
genuine fresh rebuy (each new `run_full_lifecycle()` call starts
`current_hwm = ORIGINAL_ACCOUNT_BALANCE` fresh, correct semantics).
Cushion mode FORCIBLY BYPASSES the sim_cache entirely (caching is
fundamentally incompatible with path-dependent state — confirmed
reasoning from when this was first flagged) — expect cushion-mode sweeps
to be meaningfully slower than constant-risk ones as a direct,
accepted consequence.

**Verified with real data:** all 4 return-signature combinations
(log_trades x return_final_hwm) tested and match expected shapes exactly.
HWM persistence demonstrably changes simulation behavior — two runs
differing only in external_hwm (7000 vs fresh-at-6000) produced
different daily returns from day 1, confirming the mechanism isn't a
no-op.

**Real discrepancy found and investigated, not hidden:** re-running the
existing single-lifecycle smoke test under sizing_mode='constant' (which
should be bit-identical to before this session's edits) now shows Step 1
breaching at equity $4,442.00, not the previously-reported $4,468.22.
Investigated thoroughly: (1) standalone p32_trend_engine.py smoke test
still matches the confirmed cost-model-fix baseline exactly
(17.24%/0.258/-24.77%) — the underlying full-history return series is
unchanged; (2) a from-scratch manual replication of _run_segment's exact
governance logic (daily/static breach checks), walked independently
against that same confirmed return series, ALSO produces $4,442.00, not
$4,468.22. The current value is internally self-consistent and
independently reproducible through two different code paths. Could not
identify what produced $4,468.22 in the earlier report, or rule out that
it came from a transient/different code state that was never as
rigorously cross-checked at the time. Treating $4,442.00 as the verified
baseline going forward — it has stronger evidence behind it than the
number it's replacing — but flagging this honestly rather than silently
overwriting history.

**Structural end-to-end tests, cushion mode:** at 1.2% risk, never passed
Step 1 across the entire available ~7-year history (consistent with
cushion sizing being meaningfully more conservative — smaller positions
during any drawdown slow reaching the 8% target). At 2.5% risk, attempt 1
progressed through Step 1 into Step 2 before breaching (BREACH_DAILY) —
confirms the Step1->Step2 HWM handoff works correctly across a real
transition, further than any earlier cushion test reached. Did not reach
the funded/M15 stage in this local testing (limited by available time and
this sandbox's lack of real M15 data anyway) — but the wiring is
identical to constant mode's already-confirmed funded-stage path (Goat
Guard operates on trade_log data regardless of how positions were sized),
so no reason to expect divergent behavior there specifically.

**Status: cushion sizing is now genuinely safe to use through the full
pipeline** (`sizing_mode="cushion"` on `run_lifecycle_chain`/
`run_full_lifecycle`), with the understood cost that it bypasses caching
entirely and will be meaningfully slower than constant-risk sweeps.
`ensemble_ev.py` does NOT yet expose this option — threading it through
the sweep functions there is the next concrete step before an actual
constant-vs-cushion comparison sweep can run.

## 2026-09-02 — MAJOR CORRECTION: POINT_SIZE was wrong by ~10x for every FX instrument
Confirmed via live-account pull (`data/pull_point_size.py`,
`symbol_info().point`): every FX instrument's assumed point size was
wrong, off by almost exactly 10x, same direction (assumed too large) —
USDJPY/EURJPY assumed 0.01, actual 0.001; AUDCAD/EURUSD/USDCAD/GBPUSD
assumed 0.0001, actual 0.00001. Only WTI's assumed value (0.01) happened
to be correct. Corrected the `POINT_SIZE` dict in `p32_trend_engine.py`.

**Magnitude, confirmed via smoke test (same path used throughout this
session):**

| | Zero-cost (pre-cost-fix) | "Corrected" (wrong point sizes) | Actually correct (fixed point sizes) |
|---|---|---|---|
| Total return | 244.90% | 17.24% | **206.26%** |
| Sharpe | 1.978 | 0.258 | **1.779** |
| Max DD | -11.03% | -24.77% | **-11.24%** |

The genuinely-correct result sits far closer to the zero-cost baseline
than to the "corrected" numbers that drove every sweep since the cost
model was first fixed. **Real vol-scaled costs have a real but modest
effect on this strategy — the dramatic near-collapse narrative from
earlier this session was substantially a point-size bug, not a property
of the strategy.**

**Consequence, stated as plainly as the earlier zero-cost correction
was:** the entire 1.40%-1.50% "clean plateau, statistically trustworthy"
finding — the wide sweep, both focused resweeps, the truncation-bias
fix's validation, all of it — was computed against costs overstated by
roughly 10x for 6 of 7 instruments. This is not a result to treat as
"directionally right, magnitude uncertain." It solved a meaningfully
different, more pessimistic problem than the real one. A full fresh wide
sweep (0.3%-2.5% or similar) is required — the true optimal risk level
is unknown again, not narrowed to a known-good band.

## 2026-09-02 — 1.4% locked as working risk level; cushion-sizing comparison script built
Direct instruction: sticking with 1.4% (constant-risk) as the working
answer rather than pursuing further resolution — genuine diminishing
returns after two confirmed real anomalies (2.00%, 1.55-1.60%) both
resolved through this project's now-established resweep method.

Built `compare_sizing_modes.py` — direct constant-vs-cushion comparison
at the LOCKED 1.4% base risk, identical starting points for both modes
(apples-to-apples, not two different samples). Deliberately a SEPARATE
script from ensemble_ev.py, not a mode added to it: cushion sizing
bypasses the sim_cache entirely (necessary for correctness — caching is
incompatible with path-dependent HWM state), meaning every segment
triggers a fresh full-history simulation. The full 256-point/17-level
sweep architecture used for constant-risk sizing is not tractable for
cushion mode. Uses a deliberately reduced 42-day spacing (chosen before
running, not tuned) — a genuine first-pass comparison, explicitly NOT
claimed to have the same statistical power as the full constant-risk
sweeps.

**Structural test result (7 points, wide 400-day spacing, this sandbox's
usual missing-M15-data limitation applies):** confirms correct
parallelization, correct per-item error handling, correct mode-based
aggregation — no crashes. Also surfaced a real, small-sample signal
worth flagging before the full run: cushion mode's paths mostly ran out
of history (DATA_EXHAUSTED) before reaching funded stage at all, while
constant mode reached funded stage often enough to hit the expected
missing-M15 error. Consistent with cushion sizing being much slower to
rebuild size after any drawdown — raises a real possibility that 1.4%
(tuned for constant-risk) may not be the right base risk to test cushion
sizing AT — a different question than what was optimized for constant
sizing. Worth keeping in mind when reading the full comparison's result.

## OPEN / BLOCKING — not yet resolved
1. Instrument universe for this account size — the $10k account's
   7-instrument set (`prop_firm_project`) is NOT assumed to be feasible at
   $5k. P12's own finding was that 4-5 of an original 12-instrument set
   already rounded to zero lots at $10k; a smaller account is a real
   capacity risk for a subset of what's left. BLOCKED on $5k GOAT-model
   contract specs, which are blocked on purchasing the account.
2. M15 history depth for the candidate universe — pull not yet run.
3. Account cost — base vs. promo price not yet distinguished.