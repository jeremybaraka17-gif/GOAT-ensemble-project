"""
pull_contract_specs.py — pulls confirmed contract specs from the LIVE $5k
GOAT-model account. This is the last blocking item on
research/hypothesis_p32_goat_5k_ensemble.md.

Does NOT assume these match prop_firm_project's $10k 1-Step specs — per
h5b_addendum_02's lesson (WTI contract size was wrong by 10x once, caught
only by checking a real account), every value here comes from a live
symbol_info()/order_calc_margin() call against the actual account, not
copied from the other project's instrument_specs.py.

Computes REAL required margin at min lot via mt5.order_calc_margin(),
not just the static contract_size field — a flat contract size doesn't
tell you whether min lot actually clears at this account size. This is
the same capacity question P12 already found bites some instruments at
$10k; checking it explicitly here rather than assuming a $5k account
behaves the same way, better or worse.

IMPORTANT: confirms the connected account is actually the new $5k GOAT
account before printing anything else — do not trust output from this
script if that check fails or looks wrong (e.g. balance far from $5k
would mean the terminal is pointed at the wrong account).

Usage:
    python data\\pull_contract_specs.py
"""
import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed.")
    sys.exit(1)

# Same candidate universe as pull_m15_history.py — kept in sync manually
# for now since this project doesn't have a shared constants module yet.
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

EXPECTED_ACCOUNT_SIZE = 5000  # sanity check only — GFT tracks this as
                                # simulated capital, balance should be
                                # at or near this on a fresh account


def main():
    if not mt5.initialize():
        print(f"ERROR: mt5.initialize() failed: {mt5.last_error()}")
        sys.exit(1)

    account = mt5.account_info()
    if account is None:
        print("ERROR: could not read account info — is the terminal logged in?")
        mt5.shutdown()
        sys.exit(1)

    print(f"Connected: {account.company} | Account {account.login} | Server {account.server}")
    print(f"Balance: ${account.balance:,.2f} | Equity: ${account.equity:,.2f} | "
          f"Account leverage: 1:{account.leverage}\n")

    if abs(account.balance - EXPECTED_ACCOUNT_SIZE) > 100:
        print(f"WARNING: balance (${account.balance:,.2f}) is not close to the "
              f"expected ${EXPECTED_ACCOUNT_SIZE:,.2f} $5k GOAT account size.")
        print("STOP and confirm this terminal is actually connected to the NEW "
              "$5k GOAT account, not the existing $10k 1-Step account, before "
              "trusting anything below.\n")

    print("=== Contract specs per instrument ===\n")
    results = []
    for name, symbol in CANDIDATE_SYMBOLS.items():
        if not mt5.symbol_select(symbol, True):
            print(f"{name} ({symbol}): NOT AVAILABLE on this account/server\n")
            results.append({"name": name, "symbol": symbol, "status": "UNAVAILABLE"})
            continue

        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            print(f"{name} ({symbol}): symbol_info or tick unavailable\n")
            results.append({"name": name, "symbol": symbol, "status": "NO_INFO"})
            continue

        min_lot = info.volume_min
        max_lot = info.volume_max
        step = info.volume_step
        contract_size = info.trade_contract_size
        price = tick.ask if tick.ask > 0 else tick.bid

        # REAL required margin at min lot, from the broker's own margin
        # calculator — not derived by hand from contract_size/leverage,
        # since that calc can silently diverge from what the broker
        # actually enforces (different margin currency, custom margin
        # tiers, etc.)
        margin = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol, min_lot, price)

        pct_of_balance = (margin / account.balance * 100) if margin and account.balance else None

        print(f"{name} ({symbol}):")
        print(f"  contract_size = {contract_size}")
        print(f"  volume_min/max/step = {min_lot} / {max_lot} / {step}")
        print(f"  current price = {price}")
        if margin is not None:
            print(f"  margin required at min lot = ${margin:,.2f} "
                  f"({pct_of_balance:.1f}% of ${account.balance:,.2f} balance)")
        else:
            print(f"  margin required at min lot = FAILED to compute "
                  f"(last_error={mt5.last_error()})")
        print()

        results.append({
            "name": name, "symbol": symbol, "status": "OK",
            "contract_size": contract_size, "min_lot": min_lot,
            "max_lot": max_lot, "step": step, "price": price,
            "margin_at_min_lot": margin, "pct_of_balance": pct_of_balance,
        })

    mt5.shutdown()

    print("=== SUMMARY — record this in research/decisions_log.md ===")
    print(f"{'name':10s} {'status':12s} {'min_lot':>8s} {'margin@min':>12s} {'%bal':>8s}")
    for r in results:
        if r["status"] != "OK":
            print(f"{r['name']:10s} {r['status']:12s}")
            continue
        margin_str = f"${r['margin_at_min_lot']:.2f}" if r["margin_at_min_lot"] is not None else "FAILED"
        pct_str = f"{r['pct_of_balance']:.1f}%" if r["pct_of_balance"] is not None else "n/a"
        print(f"{r['name']:10s} {r['status']:12s} {r['min_lot']:>8} {margin_str:>12s} {pct_str:>8s}")

    print("\nAny instrument where margin-at-min-lot is a large fraction of "
          "balance is a real capacity concern (echoes P12's finding that "
          "some instruments round to zero / can't be sized meaningfully at "
          "smaller account sizes) — flag those specifically when reporting "
          "back, don't just paste the raw table.")


if __name__ == "__main__":
    main()