"""
p32_instrument_specs.py — confirmed contract specs for the P32 universe,
sourced from the live $5k GOAT account pull (see research/decisions_log.md,
"$5k contract specs CONFIRMED").

IMPORTANT, stated honestly: pull_contract_specs.py printed a raw
`contract_size` field per instrument in its detailed output, but only the
summary table (min_lot, margin@min_lot, %bal) was captured back into
decisions_log.md — the raw contract_size values were never logged.
NOTIONAL_PER_LOT below is DERIVED from the confirmed margin figures
(margin = notional / leverage, so notional = margin * leverage), not a
directly re-pulled field. Consistency-checked against WTI's known contract
size correction (100 barrels/lot, per prop_firm_project's
h5b_addendum_02): at ~$88/barrel and 1:20 evaluation leverage, this
derivation reproduces the confirmed $4.41 margin almost exactly, which is
a real check, not just an assumption holding together by construction.

If this ever needs to be re-verified precisely (e.g. before a live
deployment, not just a backtest), re-run pull_contract_specs.py and
capture the printed contract_size lines directly rather than continuing
to rely on this derivation.
"""

# Confirmed min lot and margin-at-min-lot from the live $5k GOAT account,
# 2026-09-01 (see decisions_log.md). Uniform min_lot = 0.01 across all 7.
MIN_LOT = 0.01

CONFIRMED_MARGIN_AT_MIN_LOT_USD = {
    "usdjpy": 10.00,
    "audcad": 7.14,
    "eurusd": 11.59,
    "usdcad": 10.00,
    "eurjpy": 11.59,
    "gbpusd": 13.54,
    "wti": 4.41,
}

# Confirmed leverage table, phase-dependent — see decisions_log.md,
# "Leverage (CONFIRMED, phase-dependent)". Asset class mapping: all 6 FX
# pairs are forex; WTI is a commodity.
ASSET_CLASS = {
    "usdjpy": "forex", "audcad": "forex", "eurusd": "forex",
    "usdcad": "forex", "eurjpy": "forex", "gbpusd": "forex",
    "wti": "commodity",
}

LEVERAGE = {
    "evaluation": {"forex": 100, "commodity": 20, "crypto": 2},
    "funded":     {"forex": 50,  "commodity": 10, "crypto": 2},
}


def get_leverage(instrument: str, phase: str) -> float:
    """phase: 'evaluation' (Step 1 or Step 2) or 'funded'."""
    if phase not in LEVERAGE:
        raise ValueError(f"phase must be 'evaluation' or 'funded', got {phase!r}")
    asset_class = ASSET_CLASS[instrument]
    return LEVERAGE[phase][asset_class]


def notional_per_lot_usd(instrument: str, phase: str = "evaluation") -> float:
    """DERIVED (see module docstring) — USD notional exposure represented
    by 1.0 lot, back-computed from confirmed margin-at-min-lot and
    confirmed leverage: margin = notional / leverage at min_lot, so
    notional_at_min_lot = margin * leverage, then scaled from min_lot to
    a full 1.0 lot.

    NOTE: this assumes margin scales linearly with lot size (true for
    standard forex/CFD margining, not guaranteed for every broker/
    instrument — flagged as an assumption, not independently re-verified
    per instrument beyond the WTI consistency check in the module
    docstring)."""
    margin_at_min_lot = CONFIRMED_MARGIN_AT_MIN_LOT_USD[instrument]
    leverage = get_leverage(instrument, phase)
    notional_at_min_lot = margin_at_min_lot * leverage
    return notional_at_min_lot / MIN_LOT


def margin_required_usd(instrument: str, lots: float, phase: str = "evaluation") -> float:
    """Real margin required for a given lot size, using the confirmed
    phase-dependent leverage — this is the number that should gate
    position sizing, not a hardcoded assumption."""
    leverage = get_leverage(instrument, phase)
    notional = notional_per_lot_usd(instrument, phase) * lots
    return notional / leverage


if __name__ == "__main__":
    print("=== Derived notional-per-lot, both phases ===")
    print(f"{'instrument':10s} {'eval notional':>15s} {'funded notional':>17s} "
          f"{'eval margin@0.01':>18s}")
    for inst in CONFIRMED_MARGIN_AT_MIN_LOT_USD:
        eval_notional = notional_per_lot_usd(inst, "evaluation")
        funded_notional = notional_per_lot_usd(inst, "funded")
        eval_margin_check = margin_required_usd(inst, MIN_LOT, "evaluation")
        print(f"{inst:10s} {eval_notional:15,.0f} {funded_notional:17,.0f} "
              f"{eval_margin_check:18.2f}")
    print("\nThe last column should match CONFIRMED_MARGIN_AT_MIN_LOT_USD "
          "exactly (it's a round-trip check, not new information) — if it "
          "doesn't, there's a bug in the derivation above.")