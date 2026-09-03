"""
p32_trend_engine.py — position sizing, 2xATR(20) Chandelier trailing
stop, and daily portfolio P&L simulation, given a FIXED risk_per_trade
and phase. This is the reusable core the ensemble Monte Carlo (built
next) will call repeatedly across candidate risk levels and simulated
paths — it does NOT decide account-level governance (Step1/Step2/funded
transitions, daily/static drawdown halts) itself; that's
p32_multistage_sim.py's job, layered on top of this.

Ported conceptually from prop_firm_project's p17_cushion_fix.py daily
loop (position/stop/PnL mechanics) and p4_signal_workflow.py (signal/ATR
definitions), NOT a verbatim copy — this project's engine doesn't have
prop_firm_project's cushion-HWM dynamic sizing baked in here by design;
risk_per_trade is a constant input for a given simulation run, since base
risk is the actual thing P32's ensemble Monte Carlo is solving for.

HONEST FLAGS — read before trusting output for anything beyond structure
/ pipeline testing:

1. COST MODEL: FIXED AND UPGRADED 2026-09-02. Two things happened
   together: (a) a real bug — the 'spread' column was silently dropped
   in p32_signal_prep.py's data pipeline, meaning EVERY prior run in
   this project (all three ensemble sweeps) applied ZERO transaction
   cost, not the "simplified flat cost" this flag used to claim. Fixed
   by carrying 'spread' through the pipeline. (b) upgraded in the same
   pass to real vol-scaled costs per addendum_05's methodology
   (compute_vol_multiplier_series in p32_signal_prep.py), reimplemented
   fresh from that addendum's description since the original
   cost_model.py source was never available to port verbatim. Every
   sweep result reported before 2026-09-02 ran cost-free and should be
   treated as superseded, not as a baseline with a known caveat.

2. POINT_SIZE PER INSTRUMENT: CONFIRMED 2026-09-02 via live account
   symbol_info().point (data/pull_point_size.py). The previously-assumed
   standard-MT5-convention values were WRONG for every FX instrument —
   off by almost exactly 10x (assumed too large), same direction across
   the board. Only WTI's assumed value happened to be correct. Every FX
   trade's cost in every sweep run before this fix was overstated by
   roughly 10x — treat any result computed before 2026-09-02's
   POINT_SIZE correction as provisional, same as the earlier cost-model
   bug fix.

3. VOLUME_STEP IS ASSUMED = MIN_LOT (0.01). pull_contract_specs.py did
   capture volume_step but it wasn't logged in the summary table. Lots
   are rounded down to 0.01 increments here; if the real step differs
   this needs correcting.

4. NO AGGREGATE DAILY RISK CAP is applied inside this engine by default
   — prop_firm_project's 3% aggregate cap was specific to that account's
   own calibration (P9-P11) and is NOT assumed to transfer here. Pass
   `aggregate_daily_risk_cap=None` (default) for uncapped, or an explicit
   value once one is deliberately chosen and logged — never silently
   defaulted to a copied number.
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from p32_signal_prep import load_and_prep_all, INSTRUMENTS
from p32_instrument_specs import (
    MIN_LOT, notional_per_lot_usd, margin_required_usd, get_leverage,
)

STOP_ATR_MULT = 2.0  # unchanged from Arm CC — locked engine parameter, not re-tuned

# CONFIRMED via live $5k GOAT account, symbol_info().point, 2026-09-02
# (see decisions_log.md and data/pull_point_size.py). Every FX value here
# was WRONG in the previously-assumed standard-MT5-convention table below
# — off by almost exactly 10x, same direction (assumed too large) for
# every FX instrument. Only WTI happened to be correct. This means every
# FX trade's cost was overstated by roughly 10x throughout this session,
# including the dramatic Sharpe collapse that motivated the cost-model
# work and the "clean" 1.40%-1.50% plateau finding — both need to be
# treated as provisional pending a re-run with these corrected values.
#
# Old (WRONG) assumed table, kept here only for the historical record:
#   "usdjpy": 0.01, "eurjpy": 0.01,           # JPY pairs
#   "audcad": 0.0001, "eurusd": 0.0001,
#   "usdcad": 0.0001, "gbpusd": 0.0001,        # other FX
#   "wti": 0.01,
POINT_SIZE = {
    "usdjpy": 0.001, "eurjpy": 0.001,          # JPY pairs — CORRECTED (was 0.01)
    "audcad": 0.00001, "eurusd": 0.00001,
    "usdcad": 0.00001, "gbpusd": 0.00001,      # other FX — CORRECTED (was 0.0001)
    "wti": 0.01,                                # confirmed correct, unchanged
}

# Cushion-proportional sizing constants (2026-09-02). NOT copied blindly
# from prop_firm_project's Arm CC calibration, even though the numbers
# happen to match — Arm CC's D_max=0.08 was tied to THAT account's 6%
# static floor via a different (and somewhat confusing) path, since a
# separate, tighter risk_governor halt (5.4%) actually bound before
# Arm CC's own D_max ever could. THIS project has no such separate
# governor layer inside the backtest engine — the daily/static breach
# checks in p32_multistage_sim.py ARE the enforcement, directly. D_MAX
# here is chosen fresh, as a genuine safety margin below THIS account's
# own confirmed 10% static floor: 2 percentage points of buffer.
CUSHION_D_MAX = 0.08
# FLOOR baked in from the start, not discovered as a bug later — this is
# the exact fix prop_firm_project needed (research/hypothesis_p17_fix_
# cushion_trap.md) after finding an unfloored cushion mechanism could
# permanently self-halt (drawdown shrinks size -> near-zero returns ->
# equity never reclaims the HWM -> size stays near-zero forever). Same
# floor value (0.15) reused deliberately, since the mechanism it protects
# against is identical regardless of account.
CUSHION_FLOOR_FRAC = 0.15


def simulate_portfolio(prepared: dict, risk_per_trade: float, phase: str,
                        starting_equity: float, aggregate_daily_risk_cap: float = None,
                        instruments=None, log_trades: bool = False,
                        sizing_mode: str = "constant", external_hwm: float = None,
                        return_final_hwm: bool = False):
    """
    Simulates the confirmed 7-instrument long-only trend engine across
    the full available history, at a FIXED risk_per_trade and phase.

    Args:
        prepared: output of p32_signal_prep.load_and_prep_all()
        risk_per_trade: fraction of equity risked per new position (e.g.
            0.008 for 0.8%) — the variable the ensemble Monte Carlo will
            sweep across candidate values. Under sizing_mode='cushion',
            this is the BASE risk before the dynamic multiplier is
            applied, not the risk actually taken on any given day.
        phase: 'evaluation' or 'funded' — determines leverage via
            p32_instrument_specs.get_leverage(). Does NOT enforce
            Step1/Step2 profit targets or drawdown halts itself; that's
            p32_multistage_sim.py's job, calling this function per phase
            segment.
        starting_equity: account equity at the start of this simulation
            segment (e.g. $5,000 for a fresh Step 1 attempt, or whatever
            equity a path has after passing Step 1, for the Step 2 call).
        aggregate_daily_risk_cap: fraction of equity, or None for
            uncapped. See docstring flag #4 — must be explicitly chosen,
            never silently defaulted.
        sizing_mode: 'constant' (default — risk_per_trade applied as-is,
            every entry, regardless of drawdown state — the ORIGINAL
            engine behavior, unchanged, still the default so nothing
            that already calls this function without specifying
            sizing_mode is affected) or 'cushion' (Arm CC-style dynamic
            sizing: size_mult = clip(1 - drawdown_from_HWM/CUSHION_D_MAX,
            CUSHION_FLOOR_FRAC, 1.0), applied to risk_per_trade at every
            new entry — self-throttles as equity draws down from its
            running high-water-mark, floored so it can never reach a
            permanent-halt trap).
        external_hwm: FIX (2026-09-02) for the HWM-persistence problem
            flagged when cushion sizing was first built. Under
            sizing_mode='cushion', a segment-by-segment call structure
            (Step1 -> Step2 -> every funded cycle, each its OWN call to
            this function) previously reset the high-water-mark to
            starting_equity on EVERY call — wrong, since the mechanism
            needs continuous HWM tracking across an entire account
            lifecycle, resetting only on a genuine fresh rebuy. Pass the
            PRIOR segment's final HWM (from a previous call's
            return_final_hwm=True result) here to carry it forward
            correctly. None (default) starts fresh at starting_equity —
            correct behavior for the FIRST segment of a new lifecycle
            attempt, or for sizing_mode='constant' where this parameter
            is unused entirely.
        return_final_hwm: if True, the final equity_hwm reached during
            this call is included in the return value (see Returns
            below) — the caller (p32_multistage_sim.py) is responsible
            for threading it into the NEXT segment's external_hwm.

    Returns:
        pd.Series of daily fractional portfolio returns by default.
        If log_trades and return_final_hwm are both False (the original
        default): just the Series, unchanged from before this fix.
        Additional return elements are appended in the order
        (trade_log, final_hwm) for whichever combination of the two
        flags is True — e.g. log_trades=True, return_final_hwm=False
        returns (series, trade_log); both True returns
        (series, trade_log, final_hwm). Preserves exact backward
        compatibility for every existing caller that doesn't pass these
        new parameters.
    """
    if sizing_mode not in ("constant", "cushion"):
        raise ValueError(f"sizing_mode must be 'constant' or 'cushion', got {sizing_mode!r}")

    instruments = instruments or INSTRUMENTS
    # Align to the common date range across all instruments with valid
    # signal+ATR data — earlier instruments may have longer history than
    # WTI; only simulate over dates where ALL instruments could plausibly
    # have a live signal, avoiding a partial-universe warmup period.
    valid_frames = {inst: prepared[inst].dropna(subset=["atr20", "signal"]) for inst in instruments}
    common_start = max(df.index.min() for df in valid_frames.values())
    common_end = min(df.index.max() for df in valid_frames.values())
    all_dates = pd.date_range(common_start, common_end, freq="D")
    all_dates = [d for d in all_dates if any(d in valid_frames[i].index for i in instruments)]

    equity = starting_equity
    # external_hwm carries the running HWM forward across segment calls
    # under sizing_mode='cushion' — see docstring. None means start
    # fresh (correct for a new lifecycle attempt, or irrelevant under
    # 'constant' mode).
    equity_hwm = external_hwm if external_hwm is not None else starting_equity
    equity_hwm = max(equity_hwm, starting_equity)  # HWM can never be below where we're starting
    positions = {inst: None for inst in instruments}
    daily_returns = []
    dates_out = []
    trade_log = []

    for date in all_dates:
        day_pnl_frac = 0.0

        # cushion size multiplier for THIS day's new entries — computed
        # once per day, applied uniformly to every entry that happens
        # today. Under sizing_mode='constant' this stays fixed at 1.0,
        # i.e. risk_per_trade is applied exactly as given, unchanged
        # from the original engine behavior.
        if sizing_mode == "cushion":
            equity_hwm = max(equity_hwm, equity)
            current_dd = max(0.0, (equity_hwm - equity) / equity_hwm) if equity_hwm > 0 else 0.0
            cushion_size_mult = float(np.clip(1 - current_dd / CUSHION_D_MAX, CUSHION_FLOOR_FRAC, 1.0))
        else:
            cushion_size_mult = 1.0

        # --- current aggregate risk from open positions (ATR-implied),
        # used both for margin/risk-cap gating on new entries and just
        # to have on hand for eventual reporting ---
        risk_used_today = 0.0
        for inst in instruments:
            pos = positions[inst]
            if pos is None or date not in valid_frames[inst].index:
                continue
            atr_val = valid_frames[inst].loc[date, "atr20"]
            price = valid_frames[inst].loc[date, "close"]
            if pd.isna(atr_val) or pd.isna(price) or price <= 0:
                continue
            risk_used_today += pos["notional_usd"] * (atr_val / price)

        # --- manage existing positions: trailing stop update, exit checks ---
        for inst in instruments:
            if date not in valid_frames[inst].index:
                continue
            row = valid_frames[inst].loc[date]
            price, high, low, atr_val, sig = row["close"], row["high"], row["low"], row["atr20"], row["signal"]
            if pd.isna(price) or pd.isna(atr_val) or atr_val <= 0:
                continue

            pos = positions[inst]
            if pos is not None:
                pos["highest_since_entry"] = max(pos["highest_since_entry"], high)
                candidate_stop = pos["highest_since_entry"] - STOP_ATR_MULT * atr_val
                pos["stop"] = max(pos["stop"], candidate_stop)

                exit_price = None
                if low <= pos["stop"]:
                    exit_price = min(pos["stop"], price)
                elif sig != 1.0:
                    exit_price = price

                if exit_price is not None:
                    price_ret = (exit_price - pos["entry"]) / pos["entry"]
                    frac_of_equity = pos["notional_usd"] / equity if equity > 0 else 0
                    spread_cost = _spread_cost_frac(inst, valid_frames[inst], date, price)
                    pnl_frac = price_ret * frac_of_equity - spread_cost * frac_of_equity
                    day_pnl_frac += pnl_frac
                    if log_trades:
                        trade_log.append({
                            "instrument": inst, "entry_date": pos["entry_date"], "exit_date": date,
                            "entry_price": pos["entry"], "exit_price": exit_price,
                            "notional_usd": pos["notional_usd"],
                            "pnl_frac_of_equity_at_exit": pnl_frac,
                        })
                    positions[inst] = None
                    continue  # don't also accrue a same-day mark below for a position just closed

                # still open: accrue today's close-to-close mark
                prev_row_idx = valid_frames[inst].index.get_loc(date)
                if prev_row_idx > 0:
                    prev_close = valid_frames[inst].iloc[prev_row_idx - 1]["close"]
                    if prev_close and not pd.isna(prev_close):
                        frac_of_equity = pos["notional_usd"] / equity if equity > 0 else 0
                        day_pnl_frac += (price - prev_close) / prev_close * frac_of_equity

            # --- new entry check (only if flat after the exit logic above) ---
            if positions[inst] is None and sig == 1.0 and risk_per_trade > 0:
                stop_dist = STOP_ATR_MULT * atr_val
                # cushion_size_mult is 1.0 under sizing_mode='constant',
                # so this is a no-op there — identical to the original
                # engine's risk_dollars = equity * risk_per_trade
                risk_dollars = equity * risk_per_trade * cushion_size_mult
                notional_per_lot = notional_per_lot_usd(inst, phase)
                if notional_per_lot <= 0:
                    continue

                # size from risk budget
                lots_raw = risk_dollars / (stop_dist / price * notional_per_lot) if price > 0 else 0

                # cap by available margin at this phase's leverage
                margin_per_lot = margin_required_usd(inst, 1.0, phase)
                if margin_per_lot > 0:
                    lots_raw = min(lots_raw, equity / margin_per_lot)

                # cap by aggregate daily risk budget, if one was set
                if aggregate_daily_risk_cap is not None:
                    remaining_budget = max(0.0, aggregate_daily_risk_cap * equity - risk_used_today)
                    risk_per_lot = (atr_val / price) * notional_per_lot
                    if risk_per_lot > 0:
                        lots_raw = min(lots_raw, remaining_budget / risk_per_lot)

                lots = np.floor(lots_raw / MIN_LOT) * MIN_LOT
                if lots < MIN_LOT:
                    continue  # too small to size — recorded implicitly as no trade

                notional_usd = notional_per_lot * lots
                positions[inst] = {
                    "entry": price, "stop": price - stop_dist,
                    "highest_since_entry": price, "notional_usd": notional_usd,
                    "entry_date": date,
                }
                spread_cost = _spread_cost_frac(inst, valid_frames[inst], date, price)
                day_pnl_frac -= spread_cost * (notional_usd / equity if equity > 0 else 0)
                risk_used_today += notional_usd * (atr_val / price)

        equity *= (1 + day_pnl_frac)
        daily_returns.append(day_pnl_frac)
        dates_out.append(date)

    result = pd.Series(daily_returns, index=dates_out, name="portfolio_return")
    if log_trades:
        # Positions still open at series end never hit the exit branch
        # above, so they'd otherwise be silently absent from trade_log —
        # a real gap for Goat Guard reconstruction, which needs to know
        # about exposure that was live through the end of a segment, not
        # just closed trades. Appending them here with still_open=True
        # and end_date=the last simulated date.
        last_date = dates_out[-1] if dates_out else None
        for inst, pos in positions.items():
            if pos is not None:
                trade_log.append({
                    "instrument": inst, "entry_date": pos["entry_date"], "exit_date": last_date,
                    "entry_price": pos["entry"], "exit_price": None,
                    "notional_usd": pos["notional_usd"],
                    "pnl_frac_of_equity_at_exit": None,
                    "still_open": True,
                })
        if return_final_hwm:
            return result, trade_log, equity_hwm
        return result, trade_log
    if return_final_hwm:
        return result, equity_hwm
    return result


def _spread_cost_frac(inst: str, df: pd.DataFrame, date, price: float) -> float:
    """
    VOL-SCALED cost model (fixed 2026-09-02 — see module docstring for
    the two-part history: the spread column was silently dropped
    upstream in p32_signal_prep.py, meaning this function's own
    defensive fallback fired on EVERY call across all prior runs,
    applying zero cost the whole time; now fixed AND upgraded to actual
    vol-scaling in the same pass, per addendum_05's methodology
    (compute_vol_multiplier_series in p32_signal_prep.py) rather than
    reconnecting the old flat-cost placeholder.

    Real spread (points, from the CSV) x assumed point size, as a
    fraction of price, SCALED UP by the current volatility multiplier
    (never scaled down — the recorded spread is a noisy floor, not a
    ceiling, matching addendum_05's own stated principle). Returns 0.0
    if spread or multiplier data is missing rather than raising, since a
    missing value shouldn't silently crash a multi-year simulation — but
    this means a missing-data day is treated as free to trade, worth
    knowing about if it happens often (should be rare now that both
    columns are confirmed present in the real D1 data — see
    decisions_log.md)."""
    if date not in df.index or "spread" not in df.columns or "vol_cost_multiplier" not in df.columns:
        return 0.0
    spread_points = df.loc[date].get("spread", 0)
    vol_multiplier = df.loc[date].get("vol_cost_multiplier", 1.0)
    if pd.isna(spread_points) or spread_points <= 0 or price <= 0:
        return 0.0
    if pd.isna(vol_multiplier):
        vol_multiplier = 1.0  # warmup period or missing data — floor at no scaling, not zero cost
    point_size = POINT_SIZE.get(inst, 0.0001)
    base_cost_frac = (spread_points * point_size) / price
    return base_cost_frac * vol_multiplier


if __name__ == "__main__":
    print("Loading and prepping signal data...")
    prepared = load_and_prep_all()

    print("\nRunning a smoke test: risk_per_trade=0.008, phase='evaluation', "
          "starting_equity=5000, no aggregate risk cap...\n")
    returns = simulate_portfolio(
        prepared, risk_per_trade=0.008, phase="evaluation",
        starting_equity=5000.0, aggregate_daily_risk_cap=None,
    )

    log_returns = np.log1p(returns.clip(lower=-0.999))
    sharpe = log_returns.mean() / log_returns.std() * np.sqrt(252) if log_returns.std() > 0 else float("nan")
    total_return = (1 + returns).prod() - 1
    max_dd = (returns.add(1).cumprod() / returns.add(1).cumprod().cummax() - 1).min()

    print(f"Days simulated: {len(returns)} ({returns.index.min()} to {returns.index.max()})")
    print(f"Total return: {total_return:.2%}")
    print(f"Annualized Sharpe (log returns): {sharpe:.3f}")
    print(f"Max drawdown: {max_dd:.2%}")
    print("\nThis is a SMOKE TEST for pipeline correctness, not a validated "
          "result — see the cost-model and point-size flags at the top of "
          "this file before treating these numbers as meaningful.")