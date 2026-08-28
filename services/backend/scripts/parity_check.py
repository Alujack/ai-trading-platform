"""Contract-parity harness: same request to Express and FastAPI, diff the bodies.

Implements plan 11 §7 point 6 — the check that the API contract did not drift
while the implementation language changed. Fields that legitimately vary per call
(wall-clock stamps, cache flags, live child-process output) are normalized away;
everything else must match after JSON round-tripping.

Usage:
    # 1. bring up Postgres + Redis
    docker compose up -d postgres redis

    # 2. start both runtimes against the SAME database, schedulers off
    ENABLE_PAPER_TRADING=false ENABLE_DAILY_BRIEFING=false API_PORT=4100 \
        npx tsx apps/api/src/index.ts &
    cd services/backend && ENABLE_PAPER_TRADING=false ENABLE_DAILY_BRIEFING=false \
        .venv/bin/uvicorn app.main:app --port 8100 &

    # 3. compare
    services/backend/.venv/bin/python services/backend/scripts/parity_check.py

Exits non-zero on any mismatch, so it can gate a cutover. Override the bases with
EXPRESS_BASE / FASTAPI_BASE.

KNOWN INTENTIONAL DIFFERENCE: inside `details.fieldErrors`, Zod and Pydantic word
the same rejection differently ("Required" vs "Field required"). The status code,
the `error` key and the `details` structure all match, and the dashboard reads
only `error`, so the harness compares that structure rather than the prose.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

import os

EXPRESS = os.environ.get("EXPRESS_BASE", "http://127.0.0.1:4100")
FASTAPI = os.environ.get("FASTAPI_BASE", "http://127.0.0.1:8100")

# GET cases: (path, description)
CASES = [
    ("/api/health", "health"),
    ("/api/symbols", "symbols"),
    ("/api/performance", "performance metrics"),
    ("/api/positions", "open positions + account"),
    ("/api/journal", "journal (default limit)"),
    ("/api/journal?limit=1", "journal (explicit limit)"),
    ("/api/news", "news"),
    ("/api/news?impact=HIGH", "news filtered by impact"),
    ("/api/signals", "signals (default page)"),
    ("/api/signals?limit=2&offset=0", "signals (paged)"),
    ("/api/signals?status=CLOSED", "signals filtered by status"),
    ("/api/signals?symbol=XAUUSD", "signals filtered by symbol"),
    ("/api/signals?symbol=NOSUCH", "signals empty state"),
    ("/api/signals/raw", "raw feed"),
    ("/api/signals/raw?blockedOnly=1", "raw feed blocked-only"),
    ("/api/signals/does-not-exist", "signal 404"),
    ("/api/candles?symbol=XAUUSD&timeframe=60min&limit=3", "candles + indicators"),
    ("/api/candles?symbol=XAUUSD&timeframe=daily&limit=1", "candles daily"),
    ("/api/candles?symbol=NOSUCH&timeframe=60min", "candles empty state"),
    ("/api/candles?symbol=XAUUSD&timeframe=3min", "candles bad timeframe (400)"),
    ("/api/candles?timeframe=60min", "candles missing symbol (400)"),
    ("/api/candles?symbol=XAUUSD&timeframe=60min&limit=99999", "candles limit too big (400)"),
    ("/api/config/risk", "risk config"),
    ("/api/config/risk?strategy=ict_sweep_mss&symbol=XAUUSD", "risk config scoped"),
    ("/api/config/execution", "execution map"),
    ("/api/config/raw-feed", "raw feed flag"),
    ("/api/backtests", "backtest list"),
    ("/api/backtests/does-not-exist", "backtest 404"),
    ("/api/brokers/credentials", "broker credential status"),
    ("/api/telegram", "telegram status"),
    ("/api/internal/news-alert", "news alert read"),
    ("/api/no-such-route", "unknown route 404"),
]

#: Keys whose values legitimately differ between two calls.
VOLATILE_KEYS = {
    "receivedAt",
    "generatedAt",
    "cached",
    "webhook",       # live Telegram API call; absent token → null either way
    "tail",          # backtest child-process output
    "startedAt",
    "finishedAt",
}


def normalize(value, *, in_field_errors: bool = False):
    """Drop per-call volatility so a diff shows only real contract differences.

    Inside `details.fieldErrors` the individual message STRINGS are replaced with
    a placeholder: Zod and Pydantic word the same rejection differently ("Required"
    vs "Field required"), and nothing consumes that prose. What must match — and
    still does — is the status code, the `error` key, and the shape of `details`:
    which fields failed and how many messages each carries.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in VOLATILE_KEYS:
                out[k] = "<volatile>"
            else:
                out[k] = normalize(v, in_field_errors=in_field_errors or k == "fieldErrors")
        return out
    if isinstance(value, list):
        return [normalize(v, in_field_errors=in_field_errors) for v in value]
    if in_field_errors and isinstance(value, str):
        return "<validation-message>"
    return value


def fetch(base: str, path: str):
    req = urllib.request.Request(base + path, headers={"accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.loads(res.read() or b"null")
    except urllib.error.HTTPError as err:
        body = err.read()
        try:
            return err.code, json.loads(body or b"null")
        except json.JSONDecodeError:
            return err.code, {"_raw": body.decode("utf-8", "replace")[:200]}
    except Exception as exc:  # noqa: BLE001
        return None, {"_error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    mismatches = 0
    for path, label in CASES:
        e_status, e_body = fetch(EXPRESS, path)
        f_status, f_body = fetch(FASTAPI, path)
        e_norm, f_norm = normalize(e_body), normalize(f_body)

        status_ok = e_status == f_status
        body_ok = e_norm == f_norm

        if status_ok and body_ok:
            print(f"  MATCH  [{e_status}] {label}")
            continue

        mismatches += 1
        print(f"  DIFF   {label}   ({path})")
        if not status_ok:
            print(f"         status: express={e_status} fastapi={f_status}")
        if not body_ok:
            print(f"         express: {json.dumps(e_norm, sort_keys=True)[:600]}")
            print(f"         fastapi: {json.dumps(f_norm, sort_keys=True)[:600]}")

    print(f"\n{len(CASES) - mismatches}/{len(CASES)} cases match")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
