"""Walk-forward CLI — out-of-sample validation for the strategies worth keeping.

Slices stored candles into rolling (in-sample, out-of-sample) folds, optimises
each strategy's params on the IS window, and reports the concatenated OOS track
record — the honest estimate of live behaviour. See backtest/walkforward.py for
the methodology.

Examples
--------
    # Walk-forward trend_ema on BTC 60min (the one combo with a pulse):
    python walkforward.py --strategy trend_ema --symbol BTCUSD --timeframe 60min

    # Freeze default params and just test OOS consistency of the LIVE config:
    python walkforward.py --strategy trend_ema --symbol BTCUSD --timeframe 60min --no-optimize

    # Both survivors, all default symbols, write artifacts:
    python walkforward.py --strategy trend_ema meanrev_rsi --out ./wf_out

    # Bigger in-sample window, optimise on profit factor:
    python walkforward.py --strategy trend_ema --is-bars 2000 --oos-bars 500 --objective profit_factor
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from backtest.engine import BacktestConfig, default_cost
from backtest.loader import load_bars
from backtest.walkforward import WalkForwardResult, walk_forward
from db import close_pool, init_pool
from fetcher import SYMBOL_MAP
from indicator_calculator import normalize_timeframe

log = logging.getLogger("data.walkforward")

# Small, sensible per-strategy grids. Only knobs that act on already-computed
# indicator values (stop/target multiples, RSI thresholds) — nothing that would
# require recomputing the stored indicators.
PARAM_GRIDS: dict[str, dict[str, list]] = {
    # ICT confluence aggregator: the conviction threshold and stop buffer are the
    # live knobs. (The EURUSD in-sample backtest showed a positive edge at
    # minScore≈0.5 that flips negative at 0.65 — exactly the threshold-sensitivity
    # walk-forward exists to adjudicate.)
    "ict_confluence": {
        "minScore": [0.40, 0.50, 0.65],
        "atrBuffer": [0.3, 0.5],
    },
    # Ported xaubot classifier. Its advertised 0.55 threshold yields zero trades
    # on 2026 gold (SHORT probability peaks at 0.476, LONG is never predicted),
    # so the grid reaches below it to let walk-forward adjudicate whether the
    # in-sample edge at a forced threshold is real or a single-window artifact.
    "ml_xau": {
        "minConfidence": [0.30, 0.35, 0.40],
        "atrStopMult": [1.0, 1.5],
    },
}
DEFAULT_STRATEGIES: list[str] = ["ict_confluence"]
DEFAULT_TIMEFRAMES = ["60min"]


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


def _fmt_pf(pf: float) -> str:
    return "inf" if pf == float("inf") else f"{pf:.2f}"


def _render(res: WalkForwardResult) -> str:
    o = res.oos
    head = (
        f"\n{'='*78}\n{res.strategy} / {res.symbol} / {res.timeframe}  "
        f"[{'optimized' if res.optimized else 'fixed-params'}, obj={res.objective}]\n{'='*78}"
    )
    rows = [f"{'fold':>4} {'IS→OOS bars':>14} {'OOS n':>6} {'win%':>6} {'PF':>6} {'expR':>7} {'totR':>7}  params"]
    for i, fr in enumerate(res.folds):
        p = ",".join(f"{k}={v}" for k, v in fr.params.items()) or "(default)"
        rows.append(
            f"{i:>4} {fr.fold.is_len:>6}->{fr.fold.oos_len:<6} {fr.oos.trades:>6} "
            f"{fr.oos.win_rate:>6.1%} {_fmt_pf(fr.oos.profit_factor):>6} "
            f"{fr.oos.expectancy_r:>7.3f} {fr.oos.total_r:>7.2f}  {p}"
        )
    wfe = "n/a" if res.walk_forward_efficiency is None else f"{res.walk_forward_efficiency:.2f}"
    agg = (
        f"\nAGGREGATE OOS:  trades={o.trades}  win={o.win_rate:.1%}  PF={_fmt_pf(o.profit_factor)}  "
        f"expectancy={o.expectancy_r:.3f}R  totalR={o.total_r:.2f}  maxDD={o.max_drawdown_r:.2f}R  "
        f"maxConsecLoss={o.max_consecutive_losses}\n"
        f"profitable folds: {res.profitable_folds}/{len(res.folds)}   "
        f"walk-forward efficiency (OOS/IS edge): {wfe}"
    )
    return head + "\n" + "\n".join(rows) + "\n" + agg


def _write_outputs(out_dir: Path, results: list[WalkForwardResult]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Per-fold CSV.
    with (out_dir / "walkforward_folds.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["strategy", "symbol", "timeframe", "fold", "is_len", "oos_len",
                    "oos_trades", "oos_win_rate", "oos_profit_factor", "oos_expectancy_r",
                    "oos_total_r", "params"])
        for res in results:
            for i, fr in enumerate(res.folds):
                w.writerow([res.strategy, res.symbol, res.timeframe, i, fr.fold.is_len,
                            fr.fold.oos_len, fr.oos.trades, f"{fr.oos.win_rate:.4f}",
                            fr.oos.profit_factor, f"{fr.oos.expectancy_r:.4f}",
                            f"{fr.oos.total_r:.4f}", ";".join(f"{k}={v}" for k, v in fr.params.items())])
    # Aggregate JSON.
    summary = []
    for res in results:
        o = res.oos
        summary.append({
            "strategy": res.strategy, "symbol": res.symbol, "timeframe": res.timeframe,
            "optimized": res.optimized, "objective": res.objective,
            "folds": len(res.folds), "profitable_folds": res.profitable_folds,
            "walk_forward_efficiency": res.walk_forward_efficiency,
            "oos_trades": o.trades, "oos_win_rate": o.win_rate,
            "oos_profit_factor": (None if o.profit_factor == float("inf") else o.profit_factor),
            "oos_expectancy_r": o.expectancy_r, "oos_total_r": o.total_r,
            "oos_max_drawdown_r": o.max_drawdown_r,
            "oos_max_consecutive_losses": o.max_consecutive_losses,
            "oos_sharpe_per_trade": o.sharpe_per_trade,
        })
    (out_dir / "walkforward_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    log.info("wrote walkforward_folds.csv + walkforward_summary.json to %s", out_dir)


async def _run(args: argparse.Namespace) -> int:
    if "DATABASE_URL" not in os.environ:
        raise RuntimeError("DATABASE_URL is not set in environment")
    pool = await init_pool()

    symbols = args.symbols or list(SYMBOL_MAP.keys())
    timeframes = [normalize_timeframe(t) for t in (args.timeframes or DEFAULT_TIMEFRAMES)]
    strategies = args.strategy or DEFAULT_STRATEGIES

    results: list[WalkForwardResult] = []
    for strat in strategies:
        grid = PARAM_GRIDS.get(strat, {})
        for symbol in symbols:
            cfg = _build_config(args, symbol)
            for tf in timeframes:
                bars = await load_bars(pool, symbol, tf)
                need = args.is_bars + args.oos_bars
                if len(bars) < need:
                    log.info("skip %s/%s/%s — only %d bars (<%d needed for one fold)",
                             strat, symbol, tf, len(bars), need)
                    continue
                res = walk_forward(
                    strat, bars, symbol, tf, cfg,
                    is_size=args.is_bars, oos_size=args.oos_bars,
                    grid=grid, optimize=not args.no_optimize,
                    objective=args.objective, min_trades=args.min_trades,
                    anchored=args.anchored,
                )
                results.append(res)

    await close_pool()

    if not results:
        print("\nNo (strategy, symbol, timeframe) had enough bars for a fold. "
              "Backfill more history (backfill_history.py) or lower --is-bars/--oos-bars.")
        return 1

    print(f"\nWALK-FORWARD  balance={args.balance} risk/trade={args.risk}% "
          f"costs={'OFF' if args.no_costs else 'ON'} regime_gate={'OFF' if args.no_regime_gate else 'ON'}")
    print("Reading: walk-forward efficiency near/above 1.0 = robust; near 0 or negative = overfit. "
          "Want most folds profitable + positive aggregate expectancy AFTER costs.")
    for res in results:
        print(_render(res))

    if args.out:
        _write_outputs(Path(args.out), results)
        print(f"\nWrote walk-forward artifacts to {args.out}/")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--strategy", nargs="*", help=f"default: {DEFAULT_STRATEGIES}")
    p.add_argument("--symbols", nargs="*", help="default: all stored symbols")
    p.add_argument("--timeframes", nargs="*", help=f"default: {DEFAULT_TIMEFRAMES}")
    p.add_argument("--is-bars", type=int, default=1000, dest="is_bars", help="in-sample window size (bars)")
    p.add_argument("--oos-bars", type=int, default=300, dest="oos_bars", help="out-of-sample window size (bars)")
    p.add_argument("--anchored", action="store_true", help="expanding IS window (anchored) instead of rolling")
    p.add_argument("--no-optimize", action="store_true", dest="no_optimize",
                   help="freeze default params (pure OOS consistency test of the live config)")
    p.add_argument("--objective", default="expectancy_r",
                   choices=["expectancy_r", "profit_factor", "total_r"], help="IS optimisation target")
    p.add_argument("--min-trades", type=int, default=5, dest="min_trades",
                   help="min IS trades for a param set to qualify")
    p.add_argument("--balance", type=float, default=10000)
    p.add_argument("--risk", type=float, default=1.0, help="risk per trade, %% of equity")
    p.add_argument("--spread", type=float, help="override spread (price units)")
    p.add_argument("--slippage", type=float, help="override stop slippage (price units)")
    p.add_argument("--commission-bps", type=float, dest="commission_bps", help="commission per side, bps")
    p.add_argument("--no-costs", action="store_true", help="disable all costs (optimistic)")
    p.add_argument("--no-regime-gate", action="store_true", dest="no_regime_gate",
                   help="disable the regime gate (live trades ARE gated)")
    p.add_argument("--out", type=str, help="directory to write fold CSV + summary JSON")
    return p


def main() -> None:
    # Report uses →/± and box glyphs; force UTF-8 so Windows cp1252 consoles don't
    # crash on print (matches backtester.py / prepare_backtest.py).
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
