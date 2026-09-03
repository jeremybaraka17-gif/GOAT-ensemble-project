# goat_5k_ensemble_project

## What this is
A new, separate hypothesis line — NOT a copy of `prop_firm_project`. Reuses
the Arm CC engine (200-day MA signal, 2xATR(20) Chandelier trailing stop,
cushion-proportional sizing) as a starting architecture, but re-derives every
account-specific parameter for a different vehicle: GFT's **2-Step GOAT
Model, $5,000 account**, evaluated against an **ensemble-EV / rebuy-loop
objective** (P31's framing), not the single-account survival-EV objective
`prop_firm_project` was calibrated for.

Explicit design decision, stated up front: the challenge-fee structure caps
downside at account cost, which is an option-like payoff shape — but the
academic literature on option-like/high-water-mark compensation (Carpenter
2000; Guasoni; Panageas & Westerfield 2009) does NOT support "higher risk
always implies higher reward" as a starting assumption. The optimal risk
level for this vehicle is a hypothesis to test (P31-style Monte Carlo), not
an assumption to size against. See `research/decisions_log.md` for the full
reasoning trail.

## Relationship to the other two projects
- Does NOT touch `prop_firm_project`'s locked `config/strategy.yaml`,
  `risk_governor.py`, or any live account state. Fully separate config,
  separate governor thresholds, separate account.
- Reuses code patterns (governor structure, cushion sizing formula, MT5
  pull conventions) as a starting point, ported and re-parameterized here,
  not imported live from the other project.
- `xau_xag_project`'s personal-capital, always-long H5c methodology is the
  likely template for what this project migrates TO after the graduation
  trigger fires (3 payouts, locked — see decisions log) — that migration
  needs its own follow-on hypothesis once reached, not an assumption that
  the challenge-calibrated risk level is fine to keep running on real
  capital. Flagged now so it isn't forgotten later.

## Development location
Local machine first (e.g. `D:\Quant research\MODELS\goat_5k_ensemble_project`),
not the VPS. VPS deployment (`INSTANCE-202606`) is a later step, once the
hypothesis is locked and tested locally — same local-dev/VPS-live split
already used for `prop_firm_project` and `xau_xag_project`, except this
project uses local for both research AND first live-signal testing until
it's ready to migrate. Do not assume the VPS's known Python constraint
(virtualenv required, `venv`/`ensurepip` absent on that image) applies to
the local machine — confirm local behavior directly.

## Status (as of this scaffold)
BLOCKED on two independent, parallel data/spec pulls before the hypothesis
file can be locked and any backtest run:
1. M15 price history for the candidate 7-instrument universe (script
   drafted in `data/pull_m15_history.py`, not yet run / depth not yet
   confirmed).
2. $5k GOAT-model contract specs (min lot, contract size, margin) — blocked
   on purchasing the account; do not assume these match the $10k 1-Step
   specs already coded in `prop_firm_project`.

See `research/hypothesis_p32_goat_5k_ensemble.md` for the draft hypothesis
with locked/confirmed/blocked fields marked explicitly, and
`research/decisions_log.md` for a dated log of every parameter confirmed
so far and its source.

## Directory layout
```
config/          locked, version-controlled parameters (strategy.yaml)
research/        hypothesis + addendum files, decisions log
data/raw/        pulled price history (M15, D1) — gitignored, not committed
backtest/        engine code once ported (empty stub for now)
live/            live execution + governor code once specs are confirmed
```

## Discipline reminders (same as the other two projects)
- Pre-registration: falsification conditions locked before any backtest
  code runs. No parameter re-running after a test closes.
- Two-attempt-then-stop.
- Real inputs only — never invent or assume a blocked parameter to keep
  moving. A blocked hypothesis stays blocked and says so.
- OOS holdout, once defined, is touched once.
