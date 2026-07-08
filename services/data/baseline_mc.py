"""Geometry-matched random baseline — Monte-Carlo significance test (build plan §8).

``ict_confluence`` is the first strategy with a positive out-of-sample edge, but
it trades inside a specific frame (killzone window + structural stop + ≥min_rr
target). This script asks the question that separates a real edge from the frame:

    Does ict_confluence's selection beat RANDOM direction inside the SAME frame?

It runs ``ict_confluence`` once over the series, then runs ``ict_random_baseline``
(identical geometry, random direction) for N seeds, and reports where confluence's
per-trade expectancy falls in the random distribution. The baseline's own mean
expectancy is just as informative: if random entries in this frame already make
money, the "edge" is the geometry, not the ICT logic.

Reading the verdict
-------------------
* baseline mean expectancy ≈ 0 (slightly negative after costs)  → frame is neutral, good.
* confluence expectancy in the right tail (p ≤ 0.05)             → real selection edge.
* confluence expectancy inside the bulk of the random spread     → NO edge beyond the frame.
* baseline mean expectancy clearly positive                      → the frame itself is the edge.

Everything runs over the SAME bars with the SAME costs and fixed params, so it is
an apples-to-apples control (no optimisation confound). Aggregating across several
(symbol, timeframe) combos grows the comparison sample (build plan §8).

Examples
--------
    # The headline combo:
    python baseline_mc.py --symbols EURUSD --timeframes 60min --seeds 200

    # Grow the sample across combos:
    python baseline_mc.py --symbols EURUSD XAUUSD --timeframes 15min 60min --seeds 200
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import json
import logging
import os
import statistics
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from backtest.engine import BacktestConfig, default_cost, simulate
from backtest.loader import load_bars
from backtest.walkforward import OosStats, aggregate_oos
from db import close_pool, init_pool
from fetcher import SYMBOL_MAP
from indicator_calculator import normalize_timeframe
from strategies import build_strategy

log = logging.getLogger("data.baseline_mc")


def _stats(strategy_name: str, params: dict | None, bars, symbol: str, tf: str, cfg: BacktestConfig) -> OosStats:
    """Per-trade R-space stats for one full-series simulate (same units as walk-forward)."""
    trades = simulate(build_strategy(strategy_name, params), bars, symbol, tf, cfg).trades
    return aggregate_oos(trades)


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = pct / 100.0 * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


@dataclass(slots=True)
class ComboResult:
    symbol: str
    timeframe: str
    bars: int
    target_strategy: str
    target_trades: int
    target_expectancy_r: float
    target_profit_factor: float
    target_total_r: float
    baseline_seeds: int
    baseline_mean_trades: float
    baseline_mean_expectancy_r: float
    baseline_std_expectancy_r: float
    baseline_p5_expectancy_r: float
    baseline_p50_expectancy_r: float
    baseline_p95_expectancy_r: float
    # Fraction of random seeds whose expectancy ≥ the target's. Small ⇒ target is
    # in the right tail ⇒ a real edge beyond the frame.
    p_value: float
    verdict: str


def _verdict(target_exp: float, base_mean: float, base_std: float, p_value: float, target_trades: int) -> str:
    """Separate two things that are NOT the same: does the selection beat random
    (skill), and does it actually make money (profit). 'Beats random' on a
    negative-expectancy combo means real skill that the frame/costs still bleed
    away — informative, but NOT tradeable."""
    note = f"(only {target_trades} target trades — under-powered) " if target_trades < 30 else ""
    beats_random = p_value <= 0.05
    profitable = target_exp > 0
    pct = (1 - p_value) * 100
    if base_mean > 0.05:
        return f"{note}FRAME IS THE EDGE: random entries already average {base_mean:+.3f}R — geometry, not ICT logic."
    if profitable and beats_random:
        return f"{note}TRADEABLE EDGE: positive expectancy ({target_exp:+.3f}R) AND beats {pct:.0f}% of random (p={p_value:.3f})."
    if beats_random and not profitable:
        return f"{note}SKILL BUT UNPROFITABLE: beats {pct:.0f}% of random (p={p_value:.3f}) yet expectancy is {target_exp:+.3f}R — selection has signal, frame/costs bleed it away. NOT tradeable."
    if profitable and not beats_random:
        return f"{note}INSIDE THE NOISE: positive ({target_exp:+.3f}R) but within the random spread (p={p_value:.3f}) — not distinguishable from luck."
    return f"{note}NO EDGE: expectancy {target_exp:+.3f}R, inside the random spread (p={p_value:.3f})."


def _evaluate_combo(
    symbol: str, tf: str, bars, cfg: BacktestConfig, target: str,
    target_params: dict | None, seeds: int, baseline_params: dict | None = None,
    baseline: str = "ict_random_baseline",
) -> ComboResult:
    tgt = _stats(target, target_params, bars, symbol, tf, cfg)

    # Keep the frame matched: any frame param changed on the target (minRr,
    # atrBuffer, killzones, …) must also be set on the random baseline, or the
    # comparison stops isolating selection skill from the frame.
    base_extra = baseline_params or {}
    exps: list[float] = []
    trade_counts: list[int] = []
    for s in range(seeds):
        b = _stats(baseline, {"seed": s, **base_extra}, bars, symbol, tf, cfg)
        exps.append(b.expectancy_r)
        trade_counts.append(b.trades)

    exps_sorted = sorted(exps)
    ge = sum(1 for e in exps if e >= tgt.expectancy_r)
    p_value = ge / len(exps) if exps else 1.0
    base_mean = statistics.fmean(exps) if exps else 0.0
    base_std = statistics.pstdev(exps) if len(exps) > 1 else 0.0

    return ComboResult(
        symbol=symbol,
        timeframe=tf,
        bars=len(bars),
        target_strategy=target,
        target_trades=tgt.trades,
        target_expectancy_r=tgt.expectancy_r,
        target_profit_factor=(float("inf") if tgt.profit_factor == float("inf") else tgt.profit_factor),
        target_total_r=tgt.total_r,
        baseline_seeds=seeds,
        baseline_mean_trades=statistics.fmean(trade_counts) if trade_counts else 0.0,
        baseline_mean_expectancy_r=base_mean,
        baseline_std_expectancy_r=base_std,
        baseline_p5_expectancy_r=_percentile(exps_sorted, 5),
        baseline_p50_expectancy_r=_percentile(exps_sorted, 50),
        baseline_p95_expectancy_r=_percentile(exps_sorted, 95),
        p_value=p_value,
        verdict=_verdict(tgt.expectancy_r, base_mean, base_std, p_value, tgt.trades),
    )


def _render(r: ComboResult) -> str:
    pf = "inf" if r.target_profit_factor == float("inf") else f"{r.target_profit_factor:.2f}"
    return (
        f"\n{'='*78}\n{r.target_strategy} vs random baseline — {r.symbol}/{r.timeframe}  "
        f"({r.bars} bars, {r.baseline_seeds} seeds)\n{'='*78}\n"
        f"  TARGET  ({r.target_strategy}): trades={r.target_trades}  "
        f"expectancy={r.target_expectancy_r:+.3f}R  PF={pf}  totalR={r.target_total_r:+.2f}\n"
        f"  RANDOM  baseline: mean trades={r.baseline_mean_trades:.0f}  "
        f"expectancy mean={r.baseline_mean_expectancy_r:+.3f}R  std={r.baseline_std_expectancy_r:.3f}\n"
        f"          spread  p5={r.baseline_p5_expectancy_r:+.3f}R  "
        f"p50={r.baseline_p50_expectancy_r:+.3f}R  p95={r.baseline_p95_expectancy_r:+.3f}R\n"
        f"  p-value (random ≥ target): {r.p_value:.3f}\n"
        f"  → {r.verdict}"
    )


async def _run(args: argparse.Namespace) -> int:
    if "DATABASE_URL" not in os.environ:
        raise RuntimeError("DATABASE_URL is not set in environment")
    pool = await init_pool()

    symbols = args.symbols or list(SYMBOL_MAP.keys())
    timeframes = [normalize_timeframe(t) for t in (args.timeframes or ["60min"])]
    target_params = json.loads(args.target_params) if args.target_params else None
    baseline_params = json.loads(args.baseline_params) if args.baseline_params else None

    results: list[ComboResult] = []
    for symbol in symbols:
        cost = default_cost(symbol)
        cfg = BacktestConfig(
            starting_balance=Decimal(str(args.balance)),
            risk_pct=Decimal(str(args.risk)),
            cost=cost,
            apply_costs=not args.no_costs,
            regime_gating=not args.no_regime_gate,
        )
        for tf in timeframes:
            bars = await load_bars(pool, symbol, tf)
            if len(bars) < args.min_bars:
                log.info("skip %s/%s — only %d bars (<%d)", symbol, tf, len(bars), args.min_bars)
                continue
            results.append(_evaluate_combo(symbol, tf, bars, cfg, args.target, target_params, args.seeds, baseline_params, args.baseline))

    await close_pool()

    if not results:
        print("\nNo (symbol, timeframe) had enough bars. Backfill more history or lower --min-bars.")
        return 1

    print(f"\nRANDOM-BASELINE MONTE CARLO  target={args.target}  seeds={args.seeds}  "
          f"costs={'OFF' if args.no_costs else 'ON'}  regime_gate={'OFF' if args.no_regime_gate else 'ON'}")
    print("A real edge sits in the right tail of the random distribution (p ≤ 0.05). "
          "If the random baseline itself averages > 0, the frame is doing the work.")
    for r in results:
        print(_render(r))

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "baseline_mc_summary.json").write_text(
            json.dumps([asdict(r) for r in results], indent=2, default=str)
        )
        print(f"\nWrote baseline_mc_summary.json to {out}/")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--target", default="ict_confluence", help="strategy to test against random (default: ict_confluence)")
    p.add_argument("--baseline", default="ict_random_baseline", help="frame-matched random-control strategy (default: ict_random_baseline; use scalp_sniper_random for scalp_sniper)")
    p.add_argument("--target-params", dest="target_params", help='JSON param overrides for the target, e.g. \'{"minScore":0.5}\'')
    p.add_argument("--baseline-params", dest="baseline_params", help='JSON FRAME params to mirror onto the random baseline (keep frame matched), e.g. \'{"minRr":3.0}\'')
    p.add_argument("--symbols", nargs="*", help="default: all stored symbols")
    p.add_argument("--timeframes", nargs="*", help="default: 60min")
    p.add_argument("--seeds", type=int, default=200, help="number of random seeds (Monte-Carlo size)")
    p.add_argument("--min-bars", type=int, default=500, dest="min_bars", help="skip combos with fewer bars")
    p.add_argument("--balance", type=float, default=10000)
    p.add_argument("--risk", type=float, default=1.0, help="risk per trade, %% of equity")
    p.add_argument("--no-costs", action="store_true", help="disable all costs (optimistic)")
    p.add_argument("--no-regime-gate", action="store_true", dest="no_regime_gate")
    p.add_argument("--out", type=str, help="directory to write summary JSON")
    return p


def main() -> None:
    # Windows consoles default to cp1252 and choke on the report's unicode.
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
