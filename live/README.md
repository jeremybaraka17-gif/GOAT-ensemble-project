# live/ — empty on purpose

Nothing goes here until the backtest/hypothesis is resolved AND an actual
$5k GOAT account exists to confirm specs against. Anticipated contents,
ported and re-parameterized from `prop_firm_project/live/`:

- `risk_governor.py` — same NORMAL/WARNING/HALT state-machine pattern as
  `prop_firm_project`'s governor, but re-derived thresholds: 4% daily /
  10% static (not 3%/6%), PLUS a second, independent Goat Guard governor
  layer that doesn't exist in the other project at all (continuous
  cross-instrument floating-P&L check, funded-stage only).
- `instrument_specs.py` — populated only once real $5k GOAT contract specs
  are pulled from an actual account. Do not copy values from
  `prop_firm_project`'s version of this file — that project's specs are
  for a $10k 1-Step account and are not assumed to transfer.
- `p32_signal_workflow.py` — daily/intraday signal check, same
  no-EA-order-placement design as `prop_firm_project` (manual execution,
  Telegram instructions), once the engine and specs are both confirmed.

HALT/FLATTEN semantics: reuse the same lesson already learned and fixed in
`xau_xag_project/live_workflow.py` (2026-07-27 bugfix) — a governor HALT
state must explicitly block NEW positions without forcing a full
liquidation of existing ones. Build that distinction in from the start
here rather than discovering it as a bug later.
