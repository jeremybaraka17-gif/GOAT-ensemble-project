"""
pull_m15_history.py — pulls M15 price history for the candidate instrument
universe, needed specifically for M15-based Goat Guard reconstruction (see
research/decisions_log.md for why M15 was chosen over daily high/low).

REVISED (2026-09-01): the original version requested 200,000 bars in one
copy_rates_from_pos() call. Diagnosed via diagnose_symbols.py: this failed
SILENTLY across every instrument (mt5.last_error() still reported Success)
while small requests (10, 1000 bars) worked fine on the same symbols, same
server, same connection. This is a known MT5 API behavior — copy_rates_from_pos
has a per-call cap that varies by broker/terminal build and returns
None/empty past it rather than truncating or raising a clear error. Fixed
here by paginating in bounded chunks and walking backward until the server
runs out of history, rather than guessing a single "safe" bar count that
might silently break again on a different symbol or a different day.

This does NOT confirm contract specs (min lot, contract size, margin) —
that's a separate, blocked task requiring the actual $5k GOAT account
(see config/strategy.yaml, instrument_universe.status == "BLOCKED").

Usage:
    python data\pull_m15_history.py

Prints the actual returned date range per instrument — per the addendum_03
lesson from prop_firm_project (assumed 10yr D1 history on this broker
family turned out to be 7.8yr in practice), DO NOT assume the requested
depth was returned. Check the printed range and record it in
research/decisions_log.md before using this data for anything.
"""
import os
import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed. Run: pip install MetaTrader5")
    sys.exit(1)

import pandas as pd

# Candidate universe — NOT locked. See config/strategy.yaml, instrument_universe.
# NOTE on btcusd: prop_firm_project's P12 research explicitly excluded
# BTCUSD from that project's tradeable universe ("blocked at $5k /
# inconsistently tradeable at $10k") for the Arm CC survival-EV objective.
# Included here as a deliberate re-test for THIS project's different
# objective (ensemble-EV, GOAT $5k) and different engine — not an
# oversight, not an inherited assumption that it'll behave the same way.
# See research/decisions_log.md.
CANDIDATE_SYMBOLS = {
    "usdjpy": "USDJPY.x",
    "audcad": "AUDCAD.x",
    "eurusd": "EURUSD.x",
    "usdcad": "USDCAD.x",
    "eurjpy": "EURJPY.x",
    "gbpusd": "GBPUSD.x",
    "wti": "WTI.x",
    "btcusd": "BTCUSD.x",
}

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")

# Confirmed via diagnose_symbols.py: 1000 bars per call works cleanly.
# Chunk size kept well under that observed-working threshold, not pushed
# up to find the exact cap — the cap wasn't identified precisely, just
# confirmed to sit somewhere between 1,000 and 200,000. Re-testing to find
# the exact boundary would be effort spent for no real benefit; a
# conservative chunk size that's already confirmed to work is sufficient.
CHUNK_SIZE = 5000
MAX_CHUNKS = 200  # hard ceiling = 1,000,000 bars/instrument, well beyond
                   # any plausible M15 retention window — just a safety
                   # stop against an infinite loop, not an expected depth


def pull_full_history(symbol, timeframe):
    """Pages backward from the most recent bar in CHUNK_SIZE steps until
    the server returns fewer bars than requested (start of available
    history reached) or MAX_CHUNKS is hit."""
    all_chunks = []
    for page in range(MAX_CHUNKS):
        start_pos = page * CHUNK_SIZE
        rates = mt5.copy_rates_from_pos(symbol, timeframe, start_pos, CHUNK_SIZE)
        if rates is None or len(rates) == 0:
            break
        all_chunks.append(pd.DataFrame(rates))
        if len(rates) < CHUNK_SIZE:
            # server handed back a partial chunk -> this is the oldest
            # available data, stop here rather than looping further
            break
    if not all_chunks:
        return None
    df = pd.concat(all_chunks, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
    return df


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not mt5.initialize():
        print(f"ERROR: mt5.initialize() failed: {mt5.last_error()}")
        print("Check that the GFT MT5 terminal is open and logged in.")
        sys.exit(1)

    account = mt5.account_info()
    if account is not None:
        print(f"Connected: {account.company} | Account {account.login} | Server {account.server}\n")
    else:
        print("WARNING: connected to terminal but could not read account info\n")

    summary = []
    for name, symbol in CANDIDATE_SYMBOLS.items():
        if not mt5.symbol_select(symbol, True):
            print(f"WARNING: {symbol} not available/selectable on this server — skipping")
            summary.append((name, symbol, "UNAVAILABLE", None, None, 0))
            continue

        df = pull_full_history(symbol, mt5.TIMEFRAME_M15)
        if df is None or len(df) == 0:
            print(f"WARNING: no M15 data returned for {symbol} even after chunked pagination "
                  f"(last_error={mt5.last_error()})")
            summary.append((name, symbol, "NO_DATA", None, None, 0))
            continue

        out_path = os.path.join(OUTPUT_DIR, f"{name}_M15.csv")
        df.to_csv(out_path, index=False)

        n_bars = len(df)
        start, end = df["time"].min(), df["time"].max()
        print(f"{name} ({symbol}): {n_bars} bars, {start} to {end} -> {out_path}")
        summary.append((name, symbol, "OK", start, end, n_bars))

    mt5.shutdown()

    print("\n=== SUMMARY — record this in research/decisions_log.md ===")
    for name, symbol, status, start, end, n_bars in summary:
        span = f"{start} to {end} ({n_bars} bars)" if start is not None else "n/a"
        print(f"  {name:10s} {symbol:12s} {status:12s} {span}")
    print("\nDo NOT assume this depth matches D1 history depth or any prior "
          "assumption — record the actual range above before using this "
          "data for the Goat Guard reconstruction or anything else.")


if __name__ == "__main__":
    main()