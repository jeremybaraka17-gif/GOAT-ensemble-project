"""
p32_signal_workflow.py — daily signal generation for the LOCKED P32
configuration: GFT 2-Step GOAT $5k account, confirmed login 414222731
(GoatFunded-Server2), 200-day MA signal, 2xATR(20) Chandelier trailing
stop, CONSTANT-RISK sizing at 1.4% base risk (locked 2026-09-02 — see
research/decisions_log.md and hypothesis_p32_goat_5k_ensemble.md for the
full research trail this configuration is built on).

DOES NOT PLACE ANY ORDERS — same design philosophy as
prop_firm_project/live/p4_signal_workflow.py: GFT does not permit
EA-placed trades. This reads live MT5 state and sends Telegram
instructions for MANUAL execution. Recommends REAL native stop-loss
orders on every manually placed trade.

STAGE-AWARE, unlike prop_firm_project's single-stage account: consults
p32_risk_governor.py for Step1 -> Step2 -> Funded progress, daily/static
drawdown, valid-day/payout-cycle tracking, and — funded stage only —
Goat Guard, a continuous real-time floating-loss check this project's
other account has no equivalent of. This script's once-daily Goat Guard
check is a MINIMUM, not a substitute for more frequent monitoring once
funded — see the --goat-guard-only mode, intended to be scheduled more
often (e.g. every 15-30 minutes) once the account reaches that stage.

ACCOUNT-IDENTITY GUARD: aborts loudly if the connected MT5 terminal's
login doesn't match the confirmed account — the exact lesson
prop_firm_project's own governor design already learned once, applied
here from the start rather than discovered as a bug later.

Usage (run once per day, after the daily bar closes):
    python p32_signal_workflow.py
    python p32_signal_workflow.py --dry-run-console   # skip Telegram, print only
    python p32_signal_workflow.py --goat-guard-only    # frequent intraday check, funded stage only, no signal/entry logic
"""
import argparse
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 package not installed. Run: pip install MetaTrader5")
    sys.exit(1)

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.join(_here, "..", "backtest"))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_here, ".env"))
except ImportError:
    pass

from telegram_notify import send_telegram_message
from guarded_run import guarded_run
import p32_risk_governor as gov
from p32_instrument_specs import notional_per_lot_usd, margin_required_usd, MIN_LOT

# --- CONFIRMED, LOCKED account and engine parameters ---
# See research/decisions_log.md for the full dated confirmation trail on
# every value below — nothing here is a placeholder or a guess.
CONFIRMED_ACCOUNT_LOGIN = 414222731  # GoatFunded-Server2 — ABORTS if this doesn't match
TRADEABLE_INSTRUMENTS = ["usdjpy", "audcad", "eurusd", "usdcad", "eurjpy", "gbpusd", "wti"]
GFT_SYMBOLS = {
    "usdjpy": "USDJPY.x", "audcad": "AUDCAD.x", "eurusd": "EURUSD.x",
    "usdcad": "USDCAD.x", "eurjpy": "EURJPY.x", "gbpusd": "GBPUSD.x", "wti": "WTI.x",
}
MA_WINDOW = 200
ATR_WINDOW = 20
STOP_ATR_MULT = 2.0
RISK_PER_TRADE = 0.014  # LOCKED 2026-09-02 — do not change without a new
                          # addendum re-running the ensemble-EV sweep
BARS_NEEDED = MA_WINDOW + ATR_WINDOW + 10  # margin for weekends/holidays

STAGE_TO_PHASE = {  # p32_risk_governor.Stage values -> p32_instrument_specs phase argument
    "step1": "evaluation", "step2": "evaluation", "funded": "funded",
}

LOG_PATH = os.path.join(_here, "p32_live_run_log.csv")


def pull_bars(symbol, n_bars):
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, n_bars)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df.set_index("time")[["high", "low", "close"]]


def compute_atr(df, window=ATR_WINDOW):
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(window).mean().iloc[-1]


def compute_signal(df, window=MA_WINDOW):
    """Long if today's close is above its 200-day SMA, flat otherwise —
    the LOCKED signal, unchanged from backtest."""
    close = df["close"]
    if len(close) <= window:
        return np.nan
    ma = close.rolling(window).mean().iloc[-1]
    return np.sign(close.iloc[-1] - ma)


def get_open_position(symbol):
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return None
    p = positions[0]  # assume one position per instrument for this system
    return {
        "direction": 1 if p.type == 0 else -1,
        "entry_price": p.price_open,
        "current_sl": p.sl,
        "volume": p.volume,
        "ticket": p.ticket,
        "profit": p.profit,  # floating P&L for THIS position, in account currency
    }


def _connect_and_verify_identity():
    if not mt5.initialize():
        raise RuntimeError(f"mt5.initialize() failed: {mt5.last_error()}")
    account = mt5.account_info()
    if account is None:
        mt5.shutdown()
        raise RuntimeError("Connected but could not read account info.")
    if account.login != CONFIRMED_ACCOUNT_LOGIN:
        mt5.shutdown()
        raise RuntimeError(
            f"ACCOUNT IDENTITY MISMATCH: connected to login {account.login}, "
            f"expected {CONFIRMED_ACCOUNT_LOGIN}. Aborting — this script must "
            f"NEVER act against the wrong account. Check which MT5 terminal "
            f"is open and logged in."
        )
    return account


@guarded_run("goat_guard_check")
def run_goat_guard_only(dry_run_console: bool):
    """Frequent intraday check, funded stage only — reads all currently
    open positions' combined floating P&L directly from MT5 and checks
    it against the real-time Goat Guard threshold. Does NOT check
    signals or suggest new entries — this is a narrower, faster check
    meant to run more often than the once-daily full workflow."""
    account = _connect_and_verify_identity()
    state = gov._load_state()
    if state is None:
        mt5.shutdown()
        print("Governor not initialized — nothing to check yet.")
        return
    if state["stage"] != "funded":
        mt5.shutdown()
        print(f"Stage is '{state['stage']}' — Goat Guard does not apply yet, nothing to check.")
        return

    positions = mt5.positions_get()
    combined_floating_pnl = sum(p.profit for p in positions) if positions else 0.0
    equity = account.equity
    mt5.shutdown()

    gstate, diag = gov.check_goat_guard(combined_floating_pnl, equity)
    print(f"Goat Guard: {gstate.value.upper()} | floating_loss_pct={diag.get('floating_loss_pct', 0):.3%}")

    if gstate == gov.GovernorState.HALT:
        msg = (f"P32 Goat Guard TRIGGERED — {diag.get('consequence', '')}\n"
               f"Combined floating P&L: ${combined_floating_pnl:.2f} "
               f"({diag.get('floating_loss_pct', 0):.2%} of ${equity:.2f} equity)\n"
               f"Cumulative strikes: {diag.get('cumulative_strikes_after_this_check', '?')}")
        print(msg)
        if not dry_run_console:
            send_telegram_message(msg)
    elif gstate == gov.GovernorState.WARNING:
        msg = (f"P32 Goat Guard WARNING — floating loss approaching the 2% threshold\n"
               f"Combined floating P&L: ${combined_floating_pnl:.2f} "
               f"({diag.get('floating_loss_pct', 0):.2%} of ${equity:.2f} equity)\n"
               f"Consider manually reducing exposure.")
        print(msg)
        if not dry_run_console:
            send_telegram_message(msg)


@guarded_run("daily_signal_check")
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run-console", action="store_true",
                         help="Print instructions to console instead of sending Telegram")
    parser.add_argument("--goat-guard-only", action="store_true",
                         help="Run only the frequent Goat Guard check, skip signal/entry logic")
    args = parser.parse_args()

    if args.goat_guard_only:
        run_goat_guard_only(args.dry_run_console)
        return

    account = _connect_and_verify_identity()
    equity, balance = account.equity, account.balance
    print(f"Connected: {account.company} | Account {account.login} | Equity: ${equity:,.2f}\n")

    state = gov._load_state()
    if state is None:
        error_msg = ("P32 Signal Workflow FAILED\nGovernor not initialized. "
                      "Run `python p32_risk_governor.py --init` once before the first "
                      "scheduled run.")
        print(error_msg)
        mt5.shutdown()
        if not args.dry_run_console:
            send_telegram_message(error_msg)
        sys.exit(1)
    stage = state["stage"]
    phase = STAGE_TO_PHASE[stage]

    # --- governor: daily/static DD check FIRST, can veto everything below ---
    gstate, diag = gov.evaluate_daily_static(balance, equity)
    print(f"Stage: {stage.upper()} | Governor: {gstate.value.upper()} | "
          f"daily_loss={diag['daily_loss_pct']:.2%} | static_loss={diag['static_loss_pct']:.2%}\n")

    dollar_pnl_today = -diag["daily_loss_pct"] * diag["day_start_reference"]
    valid_days_count = gov.record_valid_day_if_applicable(dollar_pnl_today)

    stage_advanced = False
    new_stage = None
    if stage != "funded":
        stage_advanced, new_stage, stage_diag = gov.check_stage_progress(equity)
        if stage_advanced:
            stage, phase = new_stage, STAGE_TO_PHASE[new_stage]
    else:
        payout_result = gov.check_payout_cycle()
        if payout_result["cycle_ended"]:
            print(f"Payout cycle ended: eligible={payout_result['payout_eligible']}, "
                  f"total_payouts={payout_result['total_payouts_received']}, "
                  f"graduated={payout_result['graduated']}")
        # Goat Guard: at minimum, checked here once daily too — but see
        # the --goat-guard-only mode's docstring, this is NOT sufficient
        # on its own for a continuous, real-time GFT rule.
        positions_now = mt5.positions_get()
        combined_floating = sum(p.profit for p in positions_now) if positions_now else 0.0
        gg_state, gg_diag = gov.check_goat_guard(combined_floating, equity)
        if gg_state == gov.GovernorState.HALT:
            gstate = gov.GovernorState.HALT  # Goat Guard breach also blocks new entries

    block_new_entries = (gstate == gov.GovernorState.HALT)

    # --- pull data, compute signal/ATR per instrument ---
    all_prices_today = {}
    instrument_data = {}
    for inst in TRADEABLE_INSTRUMENTS:
        symbol = GFT_SYMBOLS[inst]
        if not mt5.symbol_select(symbol, True):
            print(f"WARNING: could not select {symbol}, skipping")
            continue
        df = pull_bars(symbol, BARS_NEEDED)
        if df is None or len(df) < MA_WINDOW + ATR_WINDOW:
            print(f"WARNING: insufficient bars for {symbol}, skipping")
            continue
        atr = compute_atr(df)
        signal = compute_signal(df)
        price = df["close"].iloc[-1]
        position = get_open_position(symbol)
        all_prices_today[inst] = price
        instrument_data[inst] = {"symbol": symbol, "atr": atr, "signal": signal,
                                   "price": price, "position": position}

    actions = []
    for inst, d in instrument_data.items():
        symbol, atr, signal, price = d["symbol"], d["atr"], d["signal"], d["price"]
        pos = d["position"]

        if pos is not None:
            # signal-reversal exit check (stop-outs handled natively by the SL order)
            if signal != 1:
                actions.append(
                    f"EXIT {inst.upper()} ({symbol})\n"
                    f"   Signal reversed. Close {pos['volume']} lots at market.\n"
                    f"   Entry was {pos['entry_price']:.5f}, current price {price:.5f}."
                )
            elif pos["current_sl"] == 0:
                actions.append(
                    f"WARNING: {inst.upper()} ({symbol}) has NO stop-loss set. "
                    f"Set one manually — this system assumes every position has a real SL order."
                )
        else:
            if block_new_entries:
                continue
            if signal == 1 and not np.isnan(atr) and atr > 0:
                stop_dist = STOP_ATR_MULT * atr
                risk_dollars = equity * RISK_PER_TRADE
                notional_per_lot = notional_per_lot_usd(inst, phase)
                if notional_per_lot <= 0:
                    continue
                lots_raw = risk_dollars / (stop_dist / price * notional_per_lot) if price > 0 else 0

                margin_per_lot = margin_required_usd(inst, 1.0, phase)
                if margin_per_lot > 0:
                    lots_raw = min(lots_raw, equity / margin_per_lot)

                lots = np.floor(lots_raw / MIN_LOT) * MIN_LOT
                if lots < MIN_LOT:
                    continue

                stop_price = price - stop_dist
                actions.append(
                    f"ENTER LONG {inst.upper()} ({symbol})\n"
                    f"   Lots: {lots:.2f}  |  Entry (~market): {price:.5f}\n"
                    f"   SET STOP-LOSS AT: {stop_price:.5f}  (2x ATR-20)\n"
                    f"   Risk if stopped: ~${risk_dollars:.0f} ({RISK_PER_TRADE*100:.1f}% of equity, "
                    f"phase={phase})"
                )

    mt5.shutdown()

    header = (f"P32 Daily Signal Check — {date.today().isoformat()}\n"
              f"Stage: {stage.upper()}{'  (JUST ADVANCED)' if stage_advanced else ''}\n"
              f"Equity: ${equity:,.2f} | Governor: {gstate.value.upper()}\n"
              f"Valid days this cycle: {valid_days_count}\n")
    if block_new_entries:
        header += "\nHALT — new entries suppressed today. Existing positions untouched.\n"
    message = header + "\n\n" + ("\n\n".join(actions) if actions else "No actions today.")

    print(message)
    if not args.dry_run_console:
        sent = send_telegram_message(message)
        print(f"\nTelegram sent: {sent}")

    log_row = pd.DataFrame([{
        "date": date.today().isoformat(), "stage": stage, "equity": equity,
        "governor_state": gstate.value, "n_actions": len(actions),
        "actions_summary": " | ".join(actions)[:500],
    }])
    log_row.to_csv(LOG_PATH, mode="a", header=not os.path.exists(LOG_PATH), index=False)
    print(f"Logged to {LOG_PATH}")


if __name__ == "__main__":
    main()