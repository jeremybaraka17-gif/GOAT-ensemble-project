"""
guarded_run.py — lightweight exception-guarding decorator for P32's live
scripts. Same rationale as prop_firm_project's version (not available to
reference directly, written fresh): an unhandled exception in a scheduled
task should trigger an alert, not just fail silently in a Task Scheduler
log nobody's watching.

Usage:
    from guarded_run import guarded_run

    @guarded_run("daily_signal_check")
    def main():
        ...
"""
import functools
import traceback

from telegram_notify import send_telegram_message


def guarded_run(task_name: str):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                tb = traceback.format_exc()
                print(f"UNHANDLED EXCEPTION in {task_name}:\n{tb}")
                send_telegram_message(
                    f"🚨 *P32 {task_name} CRASHED*\n"
                    f"`{type(e).__name__}: {e}`\n"
                    f"Check the VPS/local logs for the full traceback. "
                    f"No signal check ran — review manually if action might "
                    f"be needed before the next scheduled run."
                )
                raise
        return wrapper
    return decorator