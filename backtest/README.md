# backtest/ — empty on purpose

Nothing goes here until `research/hypothesis_p32_goat_5k_ensemble.md`
status changes from BLOCKED to LOCKED. Anticipated contents once unblocked,
ported and re-parameterized from `prop_firm_project`'s engine:

- `p32_trend_engine.py` — the Arm CC signal/stop mechanics (200-day MA,
  2xATR(20) Chandelier), re-pointed at this project's confirmed
  instrument universe and $5k contract specs once available.
- `p32_multistage_sim.py` — the new piece P31 didn't need: simulates
  Step 1 -> Step 2 -> funded as a sequential process per Monte Carlo path,
  not a single pass/breach binary.
- `goat_guard_m15.py` — M15-based intrabar reconstruction of aggregate
  floating P&L across all open positions, checked against the 2%
  threshold at each 15-minute mark. This is the genuinely new component
  relative to anything in `prop_firm_project` or `xau_xag_project` — those
  projects have no continuous, cross-instrument, intrabar risk check.
- `ensemble_ev.py` — the P31-style objective function (net EV per dollar
  of account cost across a rebuy loop), extended to include Goat Guard
  breach probability as a modeled cost and the 3-payout graduation trigger
  as a path-termination condition.

Do not start writing any of this against assumed instrument specs or an
un-pulled M15 dataset — see `research/decisions_log.md` "OPEN / BLOCKING"
section.
