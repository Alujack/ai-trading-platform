"""Backtest runner CLI.

Replays stored candles+indicators through the strategy modules and reports
expectancy / win rate / profit factor / max drawdown per (strategy, symbol,
timeframe), with a realistic cost model. This is the gate every strategy must
clear before paper or live use (CLAUDE.md: "Backtest every strategy before live
use").

Examples
--------
    # All strategies × all stored symbols × default timeframes:
    python backtester.py

    # One strategy, one symbol, custom risk and starting balance:
    python backtester.py --strategies trend_ema --symbols XAUUSD \
        --balance 10000 --risk 1

    # See what history is actually in the DB, then exit:
    python backtester.py --list

    # Disable costs to isolate raw strategy edge (optimistic):
    python backtester.py --no-costs

    # Write per-trade CSV + summary JSON:
    python backtester.py --out ./bt_out
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from backtest.engine import BacktestConfig, RunResult, default_cost, simulate
from backtest.loader import available_series, load_bars
from backtest.metrics import Metrics, summarize
from backtest.report import render_one, render_table
from backtest.store import save_run
from db import close_pool, init_pool
from fetcher import SYMBOL_MAP
from indicator_calculator import normalize_timeframe
from strategies import build_strategy

log = logging.getLogger("data.backtester")

DEFAULT_TIMEFRAMES = ["15min", "60min"]
DEFAULT_STRATEGIES = ["trend_ema", "meanrev_rsi", "scalp_ema"]


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


def _build_config(args: argparse.Namespace, symbol: str) -> BacktestConfig:
    cost = default_cost(symbol)
    if args.spread is not None:
        cost.spread = Decimal(str(args.spread))
    if args.slippage is not None:
        cost.slippage = Decimal(str(args.slippage))
    if args.commission_bps is not None:
        cost.commission_bps = Decimal(str(args.commission_bps))
    return BacktestConfig(
        starting_balance=Decimal(str(args.balance)),
        risk_pct=Decimal(str(args.risk)),
        cost=cost,
        apply_costs=not args.no_costs,
        regime_gating=not args.no_regime_gate,
    )


def _write_outputs(out_dir: Path, results: list[RunResult], metrics: list[Metrics]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    trades_path = out_dir / "trades.csv"
    with trades_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "strategy", "symbol", "timeframe", "direction", "signal_time",
                "entry_time", "exit_time", "entry_price", "stop", "target",
                "exit_price", "size", "risk_amount", "gross_pnl", "commission",
                "net_pnl", "r_multiple", "exit_reason", "hold_bars", "equity_after",
            ]
        )
        for r in results:
            for t in r.trades:
                writer.writerow(
                    [
                        t.strategy, t.symbol, t.timeframe, t.direction,
                        t.signal_time.isoformat(), t.entry_time.isoformat(),
                        t.exit_time.isoformat(), t.entry_price, t.stop, t.target,
                        t.exit_price, t.size, t.risk_amount, t.gross_pnl,
                        t.commission, t.net_pnl, t.r_multiple, t.exit_reason,
                        t.hold_bars, t.equity_after,
                    ]
                )

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps([m.as_dict() for m in metrics], indent=2, default=str))

    log.info("wrote %s (%d trades) and %s", trades_path, sum(len(r.trades) for r in results), summary_path)


async def _run(args: argparse.Namespace) -> int:
    if "DATABASE_URL" not in os.environ:
        raise RuntimeError("DATABASE_URL is not set in environment")
    pool = await init_pool()

    if args.list:
        series = await available_series(pool)
        print("\nStored candle series (symbol, timeframe, count):")
        if not series:
            print("  (none — ingest candles first: python main.py / backfill_history.py)")
        for sym, tf, n in series:
            print(f"  {sym:<10} {tf:<8} {n:>8}")
        await close_pool()
        return 0

    symbols = args.symbols or list(SYMBOL_MAP.keys())
    timeframes = [normalize_timeframe(t) for t in (args.timeframes or DEFAULT_TIMEFRAMES)]
    strategies = args.strategies or DEFAULT_STRATEGIES
    start = _parse_date(args.start)
    end = _parse_date(args.end)

    results: list[RunResult] = []
    metrics: list[Metrics] = []

    for strat_name in strategies:
        for symbol in symbols:
            cfg = _build_config(args, symbol)
            for tf in timeframes:
                bars = await load_bars(pool, symbol, tf, start=start, end=end)
                if not bars:
                    log.info("no_data strategy=%s symbol=%s tf=%s", strat_name, symbol, tf)
                    continue
                strategy = build_strategy(strat_name, None)
                result = simulate(strategy, bars, symbol, tf, cfg)
                results.append(result)
                metrics.append(summarize(result))

    saved_id: str | None = None
    if args.save_db and metrics:
        saved_id = await save_run(
            pool,
            label=args.label,
            starting_balance=args.balance,
            risk_pct=args.risk,
            costs_applied=not args.no_costs,
            config={
                "timeframes": timeframes,
                "symbols": symbols,
                "strategies": strategies,
                "spread": args.spread,
                "slippage": args.slippage,
                "commissionBps": args.commission_bps,
                "regimeGating": not args.no_regime_gate,
            },
            metrics=metrics,
            runs=results,
        )

    await close_pool()

    if not metrics:
        print(
            "\nNo data to backtest. Ingest candles + indicators first "
            "(python main.py, or backfill_history.py), then re-run.\n"
            "Use `python backtester.py --list` to see what's stored."
        )
        return 1

    gated_total = sum(r.regime_gated for r in results)
    print("\n" + "=" * 78)
    print("BACKTEST RESULTS" + ("  (costs DISABLED — optimistic)" if args.no_costs else "  (costs applied)"))
    print(f"  balance={args.balance}  risk/trade={args.risk}%")
    print(
        "  regime gate: "
        + (
            "OFF — un-gated strategy (NB: live trades ARE regime-gated)"
            if args.no_regime_gate
            else f"ON — matches live; {gated_total} candidate(s) suppressed by regime"
        )
    )
    print("=" * 78)
    for m in metrics:
        print("\n" + render_one(m))
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(render_table(metrics))
    print(
        "\nNotes: spread/slippage/commission are retail ESTIMATES (BTC ~7.5bps/side, "
        "FX & metals ~0.4bps/side) — calibrate to your broker.\nResults on <30 trades "
        "are not statistically meaningful. Always validate out-of-sample (walkforward.py)."
    )

    if args.out:
        _write_outputs(Path(args.out), results, metrics)
        print(f"\nWrote per-trade CSV + summary JSON to {args.out}/")

    if saved_id:
        print(f"\nSaved run to DB (id={saved_id}). View it at /backtests in the dashboard.")

    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--strategies", nargs="*", help=f"default: {DEFAULT_STRATEGIES}")
    p.add_argument("--symbols", nargs="*", help="default: all stored symbols")
    p.add_argument("--timeframes", nargs="*", help=f"default: {DEFAULT_TIMEFRAMES}")
    p.add_argument("--balance", type=float, default=10000, help="starting balance")
    p.add_argument("--risk", type=float, default=1.0, help="risk per trade, %% of equity")
    p.add_argument("--start", type=str, help="ISO date/time lower bound (inclusive)")
    p.add_argument("--end", type=str, help="ISO date/time upper bound (inclusive)")
    p.add_argument("--spread", type=float, help="override spread (price units)")
    p.add_argument("--slippage", type=float, help="override stop slippage (price units)")
    p.add_argument("--commission-bps", type=float, dest="commission_bps", help="commission per side, bps of notional")
    p.add_argument("--no-costs", action="store_true", help="disable all costs (optimistic)")
    p.add_argument(
        "--no-regime-gate",
        action="store_true",
        dest="no_regime_gate",
        help="disable the regime gate (measures the un-gated strategy; live trades ARE gated)",
    )
    p.add_argument("--out", type=str, help="directory to write trades.csv + summary.json")
    p.add_argument("--save-db", action="store_true", dest="save_db", help="persist this run to the BacktestRun table (shows in the dashboard)")
    p.add_argument("--label", type=str, help="optional human label for the saved run")
    p.add_argument("--list", action="store_true", help="list stored series and exit")
    return p


def main() -> None:
    # The report uses box-drawing + em-dash glyphs; Windows consoles default to
    # cp1252 and raise UnicodeEncodeError on them. Force UTF-8 so `python
    # backtester.py` works without setting PYTHONUTF8=1.
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass

    load_dotenv()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
    )
    args = _build_parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
