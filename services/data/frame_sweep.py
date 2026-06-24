"""Frame R&D sweep — can a better stop/target frame convert ICT's proven
selection skill into profit?

The random-baseline Monte Carlo showed ict_confluence picks better-than-random
direction (real skill) but only XAUUSD/60min clears costs — on BTC the skill is
strongest yet the trade still loses, because the FRAME (stop buffer + RR target)
bleeds it away. This sweep re-runs the matched MC across a grid of frame params
(minRr × atrBuffer) on the skill-rich 60-min combos, keeping the random baseline
on the SAME frame each time, and flags any setting where the target becomes BOTH
profitable AND beats random.

    python frame_sweep.py                 # default: XAUUSD + BTCUSD 60min
    python frame_sweep.py --symbols XAUUSD --seeds 150
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from backtest.engine import BacktestConfig, default_cost
from backtest.loader import load_bars
from baseline_mc import _evaluate_combo
from db import close_pool, init_pool
from indicator_calculator import normalize_timeframe

log = logging.getLogger("data.frame_sweep")

# Frame knobs to sweep. minRr = reward:risk target; atrBuffer = how far beyond
# structure the stop sits (wider = less likely to be wicked, but bigger risk).
MIN_RR_GRID = [1.5, 2.0, 2.5, 3.0]
ATR_BUFFER_GRID = [0.3, 0.5, 1.0]


def _short(target_exp: float, p_value: float) -> str:
    if target_exp > 0 and p_value <= 0.05:
        return "✅ TRADEABLE"
    if p_value <= 0.05:
        return "skill, unprofitable"
    if target_exp > 0:
        return "inside noise"
    return "no edge"


async def _run(args: argparse.Namespace) -> int:
    if "DATABASE_URL" not in os.environ:
        raise RuntimeError("DATABASE_URL is not set in environment")
    pool = await init_pool()

    symbols = args.symbols or ["XAUUSD", "BTCUSD"]
    timeframes = [normalize_timeframe(t) for t in (args.timeframes or ["60min"])]

    rows: list[dict] = []
    best: dict | None = None
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
                log.info("skip %s/%s — only %d bars", symbol, tf, len(bars))
                continue
            for rr in MIN_RR_GRID:
                for buf in ATR_BUFFER_GRID:
                    frame = {"minRr": rr, "atrBuffer": buf}
                    # frame applies to BOTH target and baseline (matched control).
                    res = _evaluate_combo(symbol, tf, bars, cfg, "ict_confluence", frame, args.seeds, frame)
                    tag = _short(res.target_expectancy_r, res.p_value)
                    row = {
                        "symbol": symbol, "timeframe": tf, "minRr": rr, "atrBuffer": buf,
                        "target_exp": res.target_expectancy_r, "target_trades": res.target_trades,
                        "target_pf": res.target_profit_factor, "p_value": res.p_value,
                        "baseline_mean": res.baseline_mean_expectancy_r, "tag": tag,
                    }
                    rows.append(row)
                    print(f"{symbol:7}/{tf:5} minRr={rr:<3} atrBuf={buf:<3}  "
                          f"exp={res.target_expectancy_r:+.3f}R PF={res.target_profit_factor:.2f} "
                          f"n={res.target_trades:<3} p={res.p_value:.3f}  {tag}")
                    if res.target_expectancy_r > 0 and res.p_value <= 0.05:
                        if best is None or res.target_expectancy_r > best["target_exp"]:
                            best = row

    await close_pool()

    print("\n" + "=" * 70)
    if best:
        print(f"BEST TRADEABLE FRAME: {best['symbol']}/{best['timeframe']} "
              f"minRr={best['minRr']} atrBuffer={best['atrBuffer']} → "
              f"{best['target_exp']:+.3f}R PF={best['target_pf']:.2f} "
              f"({best['target_trades']} trades, p={best['p_value']:.3f})")
    else:
        print("NO frame in the grid produced a profitable + beats-random combo. "
              "Selection skill is real but the param frame alone can't convert it — "
              "next lever is STRUCTURAL exits (partial TP / breakeven / trailing), "
              "which need engine support.")
    print("=" * 70)

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "frame_sweep.json").write_text(json.dumps(rows, indent=2, default=str))
        print(f"\nWrote frame_sweep.json to {out}/")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="*", help="default: XAUUSD BTCUSD")
    p.add_argument("--timeframes", nargs="*", help="default: 60min")
    p.add_argument("--seeds", type=int, default=120, help="MC seeds per frame setting")
    p.add_argument("--min-bars", type=int, default=500, dest="min_bars")
    p.add_argument("--balance", type=float, default=10000)
    p.add_argument("--risk", type=float, default=1.0)
    p.add_argument("--no-costs", action="store_true")
    p.add_argument("--no-regime-gate", action="store_true", dest="no_regime_gate")
    p.add_argument("--out", type=str, default="./bt_out/frame_sweep")
    return p


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "warning").upper(),
                        format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    args = _build_parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
