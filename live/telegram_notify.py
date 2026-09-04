"""
telegram_notify.py — lightweight, self-contained Telegram sender for
P32's live scripts. NOT copied from prop_firm_project's version of this
file (not available to reference) — written fresh, deliberately simple.
Swap this for prop_firm_project's actual telegram_notify.py if you'd
rather reuse that exact bot/infrastructure; the interface here
(send_telegram_message(text) -> bool) is designed to be a drop-in match
for how p32_signal_workflow.py calls it either way.

Requires two environment variables (put in .env alongside this project,
same convention as prop_firm_project):
    P32_TELEGRAM_BOT_TOKEN
    P32_TELEGRAM_CHAT_ID

Deliberately uses a DIFFERENT env var prefix (P32_ vs whatever
prop_firm_project uses) so the two projects' credentials can't be
accidentally cross-wired — sending a P32 GOAT account alert to the
wrong Telegram chat, or vice versa, is exactly the kind of silent
cross-project mistake this project's isolation discipline exists to
prevent.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

import urllib.request
import urllib.parse
import urllib.error
import json

BOT_TOKEN = os.environ.get("P32_TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("P32_TELEGRAM_CHAT_ID")


def send_telegram_message(text: str) -> bool:
    """Sends a message via the Telegram Bot API. Returns True on
    apparent success, False on any failure — NEVER raises, since a
    failed notification should not crash the calling script (missing a
    Telegram alert is bad; crashing the signal workflow because Telegram
    is briefly down is worse)."""
    if not BOT_TOKEN or not CHAT_ID:
        print("WARNING: P32_TELEGRAM_BOT_TOKEN / P32_TELEGRAM_CHAT_ID not set — "
              "message NOT sent, printed here instead:\n" + text)
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    # FIX (2026-09-04): parse_mode="Markdown" removed. Real failure found
    # on the first live test: Telegram's legacy Markdown mode treats a
    # bare underscore as the start of italic formatting and fails to
    # parse the whole message if it can't find a matching closing
    # underscore — the test message failed because of the underscore in
    # "telegram_notify.py" itself. This isn't a one-off: real alerts
    # routinely contain underscores (file paths, Python tracebacks,
    # variable/function names) that would trigger the same failure.
    # Worst case, a genuine crash alert from guarded_run.py could itself
    # silently fail to send because the exception text it's reporting
    # contains an underscore — exactly when a working alert matters
    # most. Sending plain text instead trades a small formatting loss
    # (literal asterisks instead of bold) for eliminating this entire
    # class of silent failure.
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID, "text": text,
    }).encode()

    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("ok", False)
    except urllib.error.HTTPError as e:
        # FIX (2026-09-04): the previous generic except swallowed
        # Telegram's own error description entirely — "Bad Request" on
        # its own gives no way to tell an invalid token apart from an
        # invalid chat ID apart from a malformed message. Telegram's API
        # returns a specific reason in the response body; read and print
        # it instead of hiding it.
        try:
            error_body = json.loads(e.read().decode())
            reason = error_body.get("description", "(no description in response)")
        except Exception:
            reason = "(could not parse error response body)"
        print(f"WARNING: Telegram send failed — HTTP {e.code}: {reason}\nMessage was:\n{text}")
        return False
    except Exception as e:
        print(f"WARNING: Telegram send failed ({e}) — message was:\n{text}")
        return False


if __name__ == "__main__":
    ok = send_telegram_message("P32 telegram_notify.py test message — if you see this, it works.")
    print(f"Send result: {ok}")