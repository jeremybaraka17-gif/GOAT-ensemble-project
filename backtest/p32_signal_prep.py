"""
p32_signal_prep.py — vectorized 200-day MA signal + ATR(20) computation
for the P32 confirmed 7-instrument universe. Ported from
prop_firm_project/live/p4_signal_workflow.py's compute_signal()/
compute_atr() functions, which only ever computed the LATEST bar's value
for live daily use. This version computes the full rolling series across
history, needed for backtesting.

Same signal definition as Arm CC, unchanged: long if close > 200-day SMA,
flat otherwise. Same ATR window (20 days). Not re-tuned for this project
— per research/hypothesis_p32_goat_5k_ensemble.md, the engine itself is
reused as-is; what's actually being tested is base risk and account
structure, not a new signal.

Data source: D1 CSVs reused from prop_firm_project (see decisions_log.md,
"D1 data reused, not re-pulled" — same broker feed, not account-specific).
Expects files at data/raw/<name>_D1.csv with columns matching the existing
project convention: time, open, high, low, close, tick_volume, spread,
real_volume.

Usage (as a module):
    from p32_signal_prep import load_and_prep_all
    prepared = load_and_prep_all()
    # prepared["usdjpy"] -> DataFrame indexed by date with columns:
    #   close, high, low, atr20, signal (1.0 / -1.0 / nan)
"""
import os
import pandas as pd
import numpy as np

MA_WINDOW = 200
ATR_WINDOW = 20

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "raw")

# Matches the confirmed P32 universe exactly — see config/strategy.yaml,
# instrument_universe. BTCUSD deliberately absent (excluded, see
# decisions_log.md).
INSTRUMENTS = ["usdjpy", "audcad", "eurusd", "usdcad", "eurjpy", "gbpusd", "wti"]


def load_d1(name: str) -> pd.DataFrame:
    """Loads a single instrument's D1 OHLC CSV, indexed by date.

    BUGFIX (2026-09-02): previously dropped the 'spread' column here,
    which meant NO downstream DataFrame ever carried it — every
    transaction cost calculation in p32_trend_engine.py silently
    returned 0.0 via its own defensive "spread not in columns" check.
    Every simulation run so far (all three ensemble sweeps) ran with
    ZERO transaction costs, not the "simplified flat cost" the code
    comments claimed. Fixed by keeping 'spread' through this function
    and prep_instrument() below."""
    path = os.path.join(DATA_DIR, f"{name}_D1.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Copy the existing D1 CSV from prop_firm_project's "
            f"data folder into data/raw/ first — see decisions_log.md "
            f"'D1 data reused, not re-pulled'."
        )
    df = pd.read_csv(path, parse_dates=["time"])
    df = df.set_index("time").sort_index()
    return df[["open", "high", "low", "close", "spread"]]


def compute_atr_series(df: pd.DataFrame, window: int = ATR_WINDOW) -> pd.Series:
    """Full rolling ATR series (simple rolling mean of True Range,
    matching prop_firm_project's compute_atr() calculation exactly — not
    switched to an exponential/Wilder-smoothed variant, since that would
    be an unreviewed change to a locked engine parameter)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def compute_signal_series(df: pd.DataFrame, window: int = MA_WINDOW) -> pd.Series:
    """Full rolling signal series. 1.0 = long (close > 200-day SMA),
    -1.0 = flat/no-position (close <= SMA), NaN until enough history
    exists for the SMA window. Matches prop_firm_project's
    compute_signal() sign logic exactly, vectorized."""
    close = df["close"]
    ma = close.rolling(window).mean()
    sig = np.sign(close - ma)
    # np.sign returns 0.0 exactly at close==ma; treat that edge case as
    # flat (-1.0 / no long signal) rather than leaving an ambiguous 0.0,
    # matching the long-only engine's convention elsewhere (signal != 1
    # means no position)
    sig = sig.replace(0.0, -1.0)
    return sig


def compute_vol_multiplier_series(close: pd.Series, cost_window: int = 20) -> pd.Series:
    """
    Vol-scaled cost multiplier, per prop_firm_project's addendum_05
    methodology (the actual algorithm, reimplemented fresh here — the
    original cost_model.py source was not available to port verbatim,
    only its described methodology, so this is a new implementation of
    that same specification, not a copy of working code):

        realized_vol_t = rolling std of log returns, window = 20 days
        baseline_vol   = full-sample MEDIAN of realized_vol (robust to
                         any single period dominating a mean-based baseline)
        vol_ratio_t    = realized_vol_t / baseline_vol
        multiplier_t   = max(1.0, vol_ratio_t)  — gamma=1.0, linear, not
                         optimized, a fixed documented default per
                         addendum_05's own stated reasoning

    Floored at 1.0 — only ever scales recorded cost UP, matching
    addendum_05: 'the recorded spread is treated as a noisy floor, not a
    ceiling.'"""
    log_returns = np.log(close / close.shift(1))
    realized_vol = log_returns.rolling(cost_window).std()
    baseline_vol = realized_vol.median()
    vol_ratio = realized_vol / baseline_vol
    multiplier = vol_ratio.clip(lower=1.0)
    return multiplier


def prep_instrument(name: str) -> pd.DataFrame:
    df = load_d1(name)
    df["atr20"] = compute_atr_series(df)
    df["signal"] = compute_signal_series(df)
    df["vol_cost_multiplier"] = compute_vol_multiplier_series(df["close"])
    return df[["close", "high", "low", "atr20", "signal", "spread", "vol_cost_multiplier"]]


def load_and_prep_all(instruments=None) -> dict:
    """Returns {name: prepped DataFrame} for every instrument in the
    confirmed universe (or a subset, if instruments is passed). Prints a
    coverage summary per instrument so date-range mismatches across the 7
    instruments are visible up front, not discovered mid-simulation."""
    instruments = instruments or INSTRUMENTS
    result = {}
    print("=== P32 signal prep — coverage per instrument ===")
    for name in instruments:
        df = prep_instrument(name)
        valid = df.dropna(subset=["atr20", "signal"])
        if len(valid):
            print(f"  {name:10s} {len(df):5d} raw bars | "
                  f"{len(valid):5d} bars with valid signal+ATR | "
                  f"{valid.index.min()} to {valid.index.max()}")
        else:
            print(f"  {name:10s} {len(df):5d} raw bars | NO VALID SIGNAL BARS")
        result[name] = df
    return result


if __name__ == "__main__":
    prepped = load_and_prep_all()
    print("\nDone. Each instrument's DataFrame has columns: "
          "close, high, low, atr20, signal.")