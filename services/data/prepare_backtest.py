"""One-command backtest prep: backfill history → compute indicators → backtest.

The pieces already exist (backfill_history.py, indicator_calculator.py,
backtester.py) but running them by hand is N symbols × M timeframes × 3 steps.
This chains them so a statistically meaningful scalp_vwap backtest is one command:

    python prepare_backtest.py --symbols XAUUSD --timeframes 1min,5min --target 6000

For each (symbol, timeframe) it backfills to `--target` bars then recomputes
indicators across all of them, then runs the backtester for `--strategies`.

Notes
-----
* Backfilling needs TWELVEDATA_API_KEY and walks the provider backward at ~8
  req/min (free tier), 5000 bars/page — a 6000-bar 1min pull is ~2 pages.
* scalp_vwap is selective + regime-gated, so aim for enough bars to clear ~30
  trades (the significance bar the backtester prints). On thin samples it will
  say "NOT SIGNIFICANT" — bump --target.
* Re-runnable: candle/indicator upserts are idempotent. Use --skip-backfill to
  only recompute indicators + backtest on what's already stored.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys

from dotenv import load_dotenv

from backfill_history import backfill
from db import close_pool
from indicator_calculator import calculate_indicators, normalize_timeframe

log = logging.getLogger("data.prepare_backtest")

# Mirrors indicator_calculator's --full: recompute across the entire stored history.
FULL_LOOKBACK = 10_000_000


def _csv(s: str) -> list[str]:
    return [p.strip() for p in s.split(",") if p.strip()]


async def _prepare(symbols: list[str], timeframes: list[str], target: int,
                   skip_backfill: bool, skip_indicators: bool) -> None:
    try:
        for symbol in symbols:
            for tf in timeframes:
                tfn = normalize_timeframe(tf)
                if not skip_backfill:
                    log.info("backfill start symbol=%s tf=%s target=%d", symbol, tfn, target)
                    await backfill(symbol, tfn, target)
                if not skip_indicators:
                    log.info("indicators start symbol=%s tf=%s (full)", symbol, tfn)
                    written = await calculate_indicators(symbol, tfn, FULL_LOOKBACK)
                    log.info("indicators done symbol=%s tf=%s rows=%d", symbol, tfn, written)
    finally:
        await close_pool()


def _run_backtest(strategies: list[str], symbols: list[str], timeframes: list[str],
                  risk: float, out: str | None) -> int:
    """Hand off to backtester.py (single source of truth for backtest logic)."""
    cmd = [
        sys.executable, "backtester.py",
        "--strategies", ",".join(strategies),
        "--symbols", ",".join(symbols),
        "--timeframes", ",".join(timeframes),
        "--risk", str(risk),
    ]
    if out:
        cmd += ["--out", out]
    log.info("backtest: %s", " ".join(cmd))
    return subprocess.call(cmd, cwd=os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    # The docstring/help contain →/×/≈; Windows consoles default to cp1252 and
    # raise UnicodeEncodeError when argparse prints them. Force UTF-8 first.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", default="XAUUSD", help="CSV, e.g. XAUUSD,EURUSD")
    parser.add_argument("--timeframes", default="1min,5min", help="CSV, e.g. 1min,5min")
    parser.add_argument("--target", type=int, default=6000, help="Bars to backfill per symbol×timeframe")
    parser.add_argument("--strategies", default="scalp_vwap", help="CSV of strategies to backtest")
    parser.add_argument("--risk", type=float, default=1.0, help="Risk %% per trade for the backtest")
    parser.add_argument("--out", default=None, help="Write per-trade CSV + summary JSON here")
    parser.add_argument("--skip-backfill", action="store_true", help="Only recompute indicators + backtest")
    parser.add_argument("--skip-indicators", action="store_true", help="Skip the indicator recompute step")
    parser.add_argument("--no-backtest", action="store_true", help="Prepare data only; don't backtest")
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
    )
    if "DATABASE_URL" not in os.environ:
        raise RuntimeError("DATABASE_URL is not set in environment")
    if not args.skip_backfill and not os.environ.get("TWELVEDATA_API_KEY"):
        raise RuntimeError("TWELVEDATA_API_KEY is not set (needed to backfill; use --skip-backfill to skip)")

    symbols, timeframes = _csv(args.symbols), _csv(args.timeframes)
    strategies = _csv(args.strategies)

    asyncio.run(_prepare(symbols, timeframes, args.target, args.skip_backfill, args.skip_indicators))

    if args.no_backtest:
        log.info("data prepared; --no-backtest set, stopping")
        return
    code = _run_backtest(strategies, symbols, timeframes, args.risk, args.out)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
