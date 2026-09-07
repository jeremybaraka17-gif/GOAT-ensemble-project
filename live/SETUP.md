# live/ — Setup & Deployment Guide

## What's in this folder
- `p32_risk_governor.py` — state tracking, daily/static DD, stage
  progress, Goat Guard. Not a script you run repeatedly by hand — other
  scripts import it.
- `p32_signal_workflow.py` — the daily script. Two modes: full daily
  check (default), and `--goat-guard-only` (funded stage, frequent).
- `telegram_notify.py`, `guarded_run.py` — supporting modules.
- `.env` — your own, not delivered (see below).
- `p32_governor_state.json` — created automatically by
  `p32_risk_governor.py --init`. This is the account's persistent memory
  — back it up occasionally, and NEVER delete it while a real challenge
  attempt is in progress (there's no way to reconstruct stage/valid-day/
  payout state from scratch).
- `p32_live_run_log.csv` — created automatically, one row per daily run.

## One-time setup (do this once, in order)

1. **Confirm the environment.** Same venv this project's backtest work
   already uses is fine — just confirm `MetaTrader5`, `python-dotenv`,
   `pandas`, `numpy` are all installed:
   ```powershell
   pip install MetaTrader5 python-dotenv pandas numpy
   ```

2. **Create `.env`** in this folder (see the earlier conversation for
   exactly what goes in it — Telegram bot token and chat ID).

3. **Test Telegram in isolation**, before touching MT5 at all:
   ```powershell
   python live\telegram_notify.py
   ```
   Confirm the test message actually arrives before moving on.

4. **Initialize the governor** — run ONCE, at the real start of the
   challenge attempt:
   ```powershell
   python live\p32_risk_governor.py --init
   ```
   If you ever need to re-initialize after a breach and rebuy (a
   genuinely new attempt, not a bug to fight), use `--force`:
   ```powershell
   python live\p32_risk_governor.py --init --force
   ```

5. **Dry-run the full workflow**, with the real MT5 terminal open and
   logged into the confirmed GOAT account:
   ```powershell
   python live\p32_signal_workflow.py --dry-run-console
   ```
   Read the output carefully. Confirm: the account-identity check
   passes silently (no abort), the stage/equity/governor line looks
   sane, and any suggested actions make sense against what's actually
   open in the terminal right now. Do NOT proceed to scheduling until
   this looks right.

6. **One real Telegram-sending test**, once step 5 looks correct:
   ```powershell
   python live\p32_signal_workflow.py
   ```
   Confirm the message arrives in the right chat, formatted sensibly.

## Scheduling (Windows Task Scheduler)

**The lesson already learned once in this project family, encoded here
so it isn't rediscovered as a bug:** this VPS/machine's Task Scheduler
requires **Interactive logon mode** for tasks that touch a running MT5
terminal — a task run under a service account or "Run whether user is
logged on or not" won't see the terminal window the same way and can
fail silently. Configure both tasks below with "Run only when user is
logged on."

Also worth checking on THIS specific machine, since it bit
`prop_firm_project` once already: confirm `DisallowStartIfOnBatteries`
isn't silently blocking execution (relevant if this runs on a laptop, not
a desktop/VPS), and that the Task Scheduler Operational event log is
enabled — `schtasks /Query /TN "<name>" /V /FO LIST` is the reliable
diagnostic if a task seems to not be firing.

### Task 1 — Daily signal check
Run once per day, after the daily D1 bar closes for your broker's server
time (confirm what time that actually is for GoatFunded-Server2 before
picking a schedule time — don't assume it matches your local midnight).

```powershell
schtasks /Create /TN "P32_Daily_Signal_Check" ^
  /TR "python \"D:\Quant research\MODELS\goat_ensemble_project\live\p32_signal_workflow.py\"" ^
  /SC DAILY /ST 00:15 /IT /RL LIMITED
```
(`/IT` = interactive logon; `/ST 00:15` is a placeholder — adjust to
actually sit after the real daily close, per the note above. `/RL
LIMITED` runs with standard, not elevated, privileges — no reason this
script needs admin rights.)

### Task 2 — Goat Guard frequent check (funded stage only)
**Do not create this task until the account actually reaches funded
stage** — running it during Step 1/Step 2 wastes cycles for no benefit
(the function itself already no-ops outside funded stage, but there's no
reason to schedule it before it's needed).

```powershell
schtasks /Create /TN "P32_Goat_Guard_Check" ^
  /TR "python \"D:\Quant research\MODELS\goat_ensemble_project\live\p32_signal_workflow.py\" --goat-guard-only" ^
  /SC MINUTE /MO 20 /IT /RL LIMITED
```
(Every 20 minutes — a judgment call, not backtested or specially
justified; more frequent than that starts to strain how "continuous" a
polling-based check can reasonably approximate GFT's real real-time
rule, less frequent starts to matter given how fast a fast-moving market
can close a 2% gap.)

## What this setup does NOT do
- Does not place any live orders — every suggested action requires
  manual execution in the terminal, by design (see
  `p32_signal_workflow.py`'s own docstring).
- Does not process a real payout withdrawal — `check_payout_cycle()`
  only tracks eligibility; requesting the actual payout through GFT's
  interface is on you.
- Does not survive a machine restart on its own — confirm Task Scheduler
  tasks are actually configured to persist across reboots if this is
  meant to run unattended for real stretches of time.