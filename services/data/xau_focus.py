"""XAU focus — fast target-only sweep to find where Gold's edge is strongest.

The random-baseline MC showed XAUUSD/60min is the only net-positive combo, but it
was thin (+0.045R, 65 trades) and only the default frame was tested. This script
sweeps frames + strategies + timeframes on XAU with the TARGET ONLY (one sim per
setting, no random baseline) so it runs in seconds — we use it to FIND promising
configs, then confirm the best one against the random baseline separately
(baseline_mc.py). Per-trade stats are R-space (same as walk-forward).

    python xau_focus.py                     # XAU 60min + daily, full grid
    python xau_focus.py --timeframes 60min
"""
from __future__ import annotations

import argparse
import asyncio
import os
from decimal import Decimal

from dotenv import load_dotenv

from backtest.engine import BacktestConfig, default_cost
from backtest.loader import load_bars
from baseline_mc import _stats
from db import close_pool, init_pool
from indicator_calculator import normalize_timeframe

# ict_confluence frame grid (minRr × atrBuffer × minScore).
RR = [2.0, 2.5, 3.0]
BUF = [0.3, 0.5]
SCORE = [0.40, 0.50]


def _configs() -> list[tuple[str, dict, str]]:
    """(strategy, params, label) — the configs to test on XAU."""
    out: list[tuple[str, dict, str]] = []
    for rr in RR:
        for buf in BUF:
            for sc in SCORE:
                out.append(("ict_confluence", {"minRr": rr, "atrBuffer": buf, "minScore": sc},
                            f"conf rr={rr} buf={buf} score={sc}"))
    # Baselines for comparison — the close-only strategies on XAU.
    out.append(("trend_ema", {}, "trend_ema default"))
    out.append(("meanrev_rsi", {}, "meanrev_rsi default"))
    return out


async def _run(args: argparse.Namespace) -> int:
    if "DATABASE_URL" not in os.environ:
        raise RuntimeError("DATABASE_URL is not set in environment")
    pool = await init_pool()
    timeframes = [normalize_timeframe(t) for t in (args.timeframes or ["60min", "daily"])]

    cost = default_cost("XAUUSD")
    cfg = BacktestConfig(
        starting_balance=Decimal(str(args.balance)),
        risk_pct=Decimal(str(args.risk)),
        cost=cost,
        apply_costs=not args.no_costs,
        regime_gating=not args.no_regime_gate,
    )

    rows = []
    for tf in timeframes:
        bars = await load_bars(pool, "XAUUSD", tf)
        print(f"\n{'='*72}\nXAUUSD / {tf}  ({len(bars)} bars)\n{'='*72}")
        print(f"{'config':28} {'trades':>6} {'win%':>6} {'expR':>7} {'PF':>6} {'totR':>7} {'maxDDr':>7}")
        results = []
        for strat, params, label in _configs():
            s = _stats(strat, params, bars, "XAUUSD", tf, cfg)
            pf = "inf" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
            print(f"{label:28} {s.trades:>6} {s.win_rate*100:>5.1f}% {s.expectancy_r:>+7.3f} "
                  f"{pf:>6} {s.total_r:>+7.2f} {s.max_drawdown_r:>7.2f}")
            results.append((tf, label, s))
            rows.append((tf, strat, params, label, s))
        # Per-TF best by expectancy among configs with a meaningful sample.
        meaningful = [(tf, label, s) for (tf, label, s) in results if s.trades >= 30]
        if meaningful:
            best = max(meaningful, key=lambda x: x[2].expectancy_r)
            print(f"  → best (≥30 trades): {best[1]}  exp={best[2].expectancy_r:+.3f}R "
                  f"PF={best[2].profit_factor:.2f} n={best[2].trades}")

    await close_pool()

    # Overall best positive-expectancy config to confirm against the random baseline.
    positives = [(tf, strat, params, label, s) for (tf, strat, params, label, s) in rows
                 if s.trades >= 30 and s.expectancy_r > 0]
    print("\n" + "=" * 72)
    if positives:
        positives.sort(key=lambda x: x[4].expectancy_r, reverse=True)
        print("TOP POSITIVE-EXPECTANCY CONFIGS (confirm these vs random baseline next):")
        for tf, strat, params, label, s in positives[:5]:
            print(f"  {tf:6} {label:28} exp={s.expectancy_r:+.3f}R PF={s.profit_factor:.2f} n={s.trades}")
        tf, strat, params, label, s = positives[0]
        print(f"\nConfirm command:\n  python baseline_mc.py --symbols XAUUSD --timeframes {tf} "
              f"--seeds 200 --target-params '{__import__('json').dumps(params)}' "
              f"--baseline-params '{__import__('json').dumps({k: params[k] for k in ('minRr','atrBuffer') if k in params})}'")
    else:
        print("NO positive-expectancy config with ≥30 trades on XAU. Edge is not in these "
              "frames/timeframes — next lever is structural exits or higher-TF data.")
    print("=" * 72)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--timeframes", nargs="*", help="default: 60min daily")
    p.add_argument("--balance", type=float, default=10000)
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--no-costs", action="store_true")
    p.add_argument("--no-regime-gate", action="store_true", dest="no_regime_gate")
    return p


def main() -> None:
    load_dotenv()
    import logging
    logging.basicConfig(level="WARNING")
    raise SystemExit(asyncio.run(_run(_build_parser().parse_args())))


if __name__ == "__main__":
    main()
