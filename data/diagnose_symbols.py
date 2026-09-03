"""
diagnose_symbols.py — run this after pull_m15_history.py returned NO_DATA
for every instrument. Isolates WHICH of the plausible causes it is, rather
than guessing:
  1. Wrong symbol names for this account's server (different account,
     414222731, possibly a different server than the .x-suffixed names
     confirmed for prop_firm_project's 414000277 / GoatFunded-Server2)
  2. Terminal hasn't cached M15 history locally yet (a real, documented
     MT5 API quirk — copy_rates_from_pos can return empty until the
     terminal has synced that timeframe, independent of whether the data
     exists on the server)
  3. Request-size cap being hit
  4. Something else — printed mt5.last_error() after every failure so
     nothing is silently swallowed this time

Usage:
    python data\\diagnose_symbols.py
"""
import sys
import time

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed.")
    sys.exit(1)


def main():
    if not mt5.initialize():
        print(f"ERROR: mt5.initialize() failed: {mt5.last_error()}")
        sys.exit(1)

    account = mt5.account_info()
    print(f"Connected: {account.company} | Account {account.login} | Server {account.server}\n")

    # --- Step 1: what does the server actually call these instruments? ---
    print("=== Step 1: searching for symbol name matches on THIS server ===")
    search_terms = ["USDJPY", "AUDCAD", "EURUSD", "USDCAD", "EURJPY", "GBPUSD", "WTI", "OIL"]
    all_symbols = mt5.symbols_get()
    if all_symbols is None:
        print(f"symbols_get() failed: {mt5.last_error()}")
    else:
        print(f"Total symbols visible on this server: {len(all_symbols)}\n")
        for term in search_terms:
            matches = [s.name for s in all_symbols if term.upper() in s.name.upper()]
            print(f"  '{term}': {matches if matches else 'NO MATCHES'}")
    print()

    # --- Step 2: for the symbol names we ORIGINALLY tried, check select + last_error ---
    print("=== Step 2: re-checking the originally-tried symbol names, with errors surfaced ===")
    original_symbols = ["USDJPY.x", "AUDCAD.x", "EURUSD.x", "USDCAD.x", "EURJPY.x", "GBPUSD.x", "WTI.x"]
    for sym in original_symbols:
        selected = mt5.symbol_select(sym, True)
        err = mt5.last_error()
        info = mt5.symbol_info(sym)
        print(f"  {sym}: select={selected}, last_error={err}, "
              f"symbol_info={'FOUND' if info else 'NOT FOUND'}")
    print()

    # --- Step 3: try a small M15 request + D1 request on the FIRST real match found ---
    print("=== Step 3: small-scale M15 vs D1 test on first confirmed-real symbol ===")
    real_symbol = None
    for term in ["USDJPY", "EURUSD"]:
        matches = [s.name for s in all_symbols if term.upper() in s.name.upper()] if all_symbols else []
        if matches:
            real_symbol = matches[0]
            break

    if real_symbol is None:
        print("No confirmed real symbol found to test against — see Step 1 output above.")
    else:
        print(f"Testing against: {real_symbol}")
        mt5.symbol_select(real_symbol, True)
        time.sleep(1)  # give the terminal a moment after selecting, before requesting history

        for tf_name, tf in [("M15", mt5.TIMEFRAME_M15), ("D1", mt5.TIMEFRAME_D1)]:
            for n in [10, 1000]:
                rates = mt5.copy_rates_from_pos(real_symbol, tf, 0, n)
                err = mt5.last_error()
                n_got = len(rates) if rates is not None else 0
                print(f"  {tf_name}, requested {n}: got {n_got} bars, last_error={err}")

    mt5.shutdown()
    print("\nDone. Paste this full output back — it will show which of the four "
          "causes listed at the top of this script is the actual one.")


if __name__ == "__main__":
    main()
