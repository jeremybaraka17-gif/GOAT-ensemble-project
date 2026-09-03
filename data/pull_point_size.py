"""
pull_point_size.py — confirms the real POINT_SIZE per instrument from the
live $5k GOAT account, closing out the last unconfirmed input feeding the
cost model. p32_trend_engine.py currently uses ASSUMED standard MT5
conventions (JPY pairs: 0.01, other FX: 0.0001, WTI: 0.01) — flagged as
unconfirmed since this project's own contract-spec pull
(pull_contract_specs.py) never captured this specific field. Given how
much the cost model now drives every result (the 2026-09-02 fix moved
Sharpe from 1.978 to 0.258 at a single risk level), an unconfirmed
point-size assumption feeding directly into every spread-cost calculation
is worth closing out properly rather than leaving as a standing guess.

MT5's symbol_info().point is the authoritative source — one tick/point in
price terms for that specific symbol on this specific account, not
inferred from a general "JPY pairs use X" convention that might not hold
for this broker's exact specification.

Usage:
    python data\\pull_point_size.py
"""
import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed.")
    sys.exit(1)

# Matches the confirmed P32 universe and the assumed values currently
# hardcoded in p32_trend_engine.py's POINT_SIZE dict — pulled here so
# they can be directly compared and corrected if they differ.
CANDIDATE_SYMBOLS = {
    "usdjpy": "USDJPY.x",
    "audcad": "AUDCAD.x",
    "eurusd": "EURUSD.x",
    "usdcad": "USDCAD.x",
    "eurjpy": "EURJPY.x",
    "gbpusd": "GBPUSD.x",
    "wti": "WTI.x",
}

ASSUMED_POINT_SIZE = {
    "usdjpy": 0.01, "eurjpy": 0.01,
    "audcad": 0.0001, "eurusd": 0.0001,
    "usdcad": 0.0001, "gbpusd": 0.0001,
    "wti": 0.01,
}


def main():
    if not mt5.initialize():
        print(f"ERROR: mt5.initialize() failed: {mt5.last_error()}")
        sys.exit(1)

    account = mt5.account_info()
    if account is not None:
        print(f"Connected: {account.company} | Account {account.login} | Server {account.server}\n")

    print(f"{'Instrument':10s} {'Confirmed point':>15s} {'Assumed point':>14s} {'Match?':>8s} {'Digits':>7s}")
    results = {}
    for name, symbol in CANDIDATE_SYMBOLS.items():
        if not mt5.symbol_select(symbol, True):
            print(f"{name:10s} NOT AVAILABLE on this account/server")
            continue
        info = mt5.symbol_info(symbol)
        if info is None:
            print(f"{name:10s} symbol_info() returned None")
            continue
        confirmed = info.point
        assumed = ASSUMED_POINT_SIZE.get(name)
        match = "YES" if abs(confirmed - assumed) < 1e-10 else "NO — DIFFERS"
        print(f"{name:10s} {confirmed:>15.6f} {assumed:>14.6f} {match:>8s} {info.digits:>7d}")
        results[name] = confirmed

    mt5.shutdown()

    print("\n=== SUMMARY — record this in research/decisions_log.md ===")
    any_mismatch = False
    for name, confirmed in results.items():
        assumed = ASSUMED_POINT_SIZE.get(name)
        if abs(confirmed - assumed) >= 1e-10:
            any_mismatch = True
            print(f"  MISMATCH: {name} confirmed={confirmed} vs assumed={assumed} "
                  f"— p32_trend_engine.py's POINT_SIZE dict needs correcting")
    if not any_mismatch:
        print("  All confirmed values match the assumed conventions currently hardcoded "
              "in p32_trend_engine.py — no code change needed, but this now closes the "
              "assumption out as CONFIRMED rather than assumed.")
    print("\nIf ANY mismatch was reported above, do not re-run the ensemble sweep until "
          "p32_trend_engine.py's POINT_SIZE dict is corrected to match these confirmed "
          "values — every prior sweep's cost calculations used the assumed values.")


if __name__ == "__main__":
    main()