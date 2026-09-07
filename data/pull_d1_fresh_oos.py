"""
pull_d1_fresh_oos.py — pulls FRESH D1 data directly from the live GOAT
account for the confirmed 7-instrument universe. Exists for exactly one
purpose: isolating the genuinely never-seen slice (anything after
2026-07-03, where every prior D1 pull in this project stopped) for a
real out-of-sample check on the LOCKED 1.4% configuration.

WHY THIS IS THE ONLY GENUINE OOS OPTION LEFT: every sweep, resweep, and
parameter fix in this project touched the ENTIRE available window
(2019-2026-07) repeatedly and iteratively. There is no untouched slice
of THAT data left — it's all informed the 1.4% decision, directly or
indirectly. Data that didn't exist yet when any of that work happened is
the only thing that can honestly be called out-of-sample now.

HONEST SCOPE: this will be a SHORT window (~2 months as of 2026-09-04) —
not enough for a statistically powerful Sharpe/EV estimate on its own.
This is a sanity check (does the signal/engine behave reasonably on data
it's never seen), not a definitive statistical test. Framed that way on
purpose, not oversold.

FALSIFICATION CONDITION, locked here BEFORE running, per this project's
own established discipline: the locked engine (1.4% risk, constant
sizing) run over ONLY the new OOS slice should show directionally
sensible behavior — no anomalous crash, no signal/data integrity issue,
return/drawdown roughly consistent with what the historical distribution
would suggest is plausible for a ~2-month window. If it's badly
inconsistent, that's a real finding requiring investigation, not
something to explain away after the fact.

Usage:
    python data\\pull_d1_fresh_oos.py
"""
import os
import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed.")
    sys.exit(1)

import pandas as pd

CANDIDATE_SYMBOLS = {
    "usdjpy": "USDJPY.x", "audcad": "AUDCAD.x", "eurusd": "EURUSD.x",
    "usdcad": "USDCAD.x", "eurjpy": "EURJPY.x", "gbpusd": "GBPUSD.x", "wti": "WTI.x",
}

# Where every prior D1 pull in this project stopped — anything after
# this date genuinely never informed any sweep or parameter decision.
PREVIOUS_DATA_CUTOFF = pd.Timestamp("2026-07-03")

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backtest", "oos_fresh")
MAX_BARS_REQUEST = 5000  # small — only need the last ~2 months, no need
                           # for the chunked-pagination approach the M15
                           # pull needed for a full multi-year history


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if not mt5.initialize():
        print(f"ERROR: mt5.initialize() failed: {mt5.last_error()}")
        sys.exit(1)

    account = mt5.account_info()
    if account is not None:
        print(f"Connected: {account.company} | Account {account.login}\n")

    print(f"Previous data cutoff (everything before this was already used "
          f"in every sweep): {PREVIOUS_DATA_CUTOFF.date()}\n")

    summary = []
    for name, symbol in CANDIDATE_SYMBOLS.items():
        if not mt5.symbol_select(symbol, True):
            print(f"WARNING: {symbol} not available — skipping")
            summary.append((name, "UNAVAILABLE", 0, None, None))
            continue

        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, MAX_BARS_REQUEST)
        if rates is None or len(rates) == 0:
            print(f"WARNING: no data returned for {symbol}")
            summary.append((name, "NO_DATA", 0, None, None))
            continue

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.set_index("time").sort_index()

        # Save the FULL pull (for continuity/overlap checking) AND a
        # separate file containing ONLY the genuinely new slice
        full_path = os.path.join(OUTPUT_DIR, f"{name}_D1_full.csv")
        df.reset_index().to_csv(full_path, index=False)

        new_slice = df[df.index > PREVIOUS_DATA_CUTOFF]
        new_path = os.path.join(OUTPUT_DIR, f"{name}_D1_oos_only.csv")
        new_slice.reset_index().to_csv(new_path, index=False)

        n_new = len(new_slice)
        if n_new > 0:
            print(f"{name}: {n_new} genuinely new bars, "
                  f"{new_slice.index.min().date()} to {new_slice.index.max().date()} -> {new_path}")
        else:
            print(f"{name}: WARNING — 0 new bars found after {PREVIOUS_DATA_CUTOFF.date()}. "
                  f"Either the broker's server date is unexpectedly behind, or something's wrong.")
        summary.append((name, "OK", n_new,
                         new_slice.index.min() if n_new else None,
                         new_slice.index.max() if n_new else None))

    mt5.shutdown()

    print("\n=== SUMMARY ===")
    for name, status, n_new, start, end in summary:
        span = f"{start.date()} to {end.date()}" if start is not None else "n/a"
        print(f"  {name:10s} {status:12s} {n_new:4d} new bars   {span}")

    print("\nUpload the *_D1_oos_only.csv files back into the chat — that's the "
          "genuinely never-seen data the real OOS check will run against.")


if __name__ == "__main__":
    main()