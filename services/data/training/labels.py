"""Triple-barrier labelling, matched bar-for-bar to the backtest engine.

The single biggest defect in the ported `xaubot` model was a label/backtest
mismatch: it trained on "will price move ±0.1% within 10 bars?" but was executed
on a 2:1 RR frame. Direction accuracy says nothing about whether a trade hits TP
before SL, so a model can be "right" and still lose money.

Here the label IS the trade outcome. For every bar we simulate the exact order
the strategy would place and ask whether it wins *after costs*:

    signal at bar i  ->  entry filled at bar i+1 OPEN (engine: `fill_bar`)
    stop   = close[i] -/+ atr_stop_mult * atr[i]
    target = entry -/+ rr * risk
    scan bars i+1 .. i+horizon intrabar (high/low); STOP WINS TIES

Every one of those details mirrors `backtest/engine.py` (`_entry_fill`,
`_exit_fill`, `_detect_exit`, `stop_first_on_ambiguous=True`) and the cost model
is imported from it rather than re-derived, so labels cannot drift from the
engine that scores the strategy.

Costs are inside the barrier maths, not applied afterwards. At 1min on gold the
round-trip cost (~$0.30 spread + $0.10 stop slippage) is a large fraction of an
ATR-sized stop, so a model trained on gross moves learns the wrong setups.

Class encoding matches the platform convention used by `ml_xau`:
    0 = SHORT, 1 = HOLD, 2 = LONG
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from backtest.engine import CostModel, default_cost

SHORT, HOLD, LONG = 0, 1, 2


# Verified against the engine on 4000 XAUUSD 15min bars with an always-LONG
# probe strategy: at horizon=200, 258/259 engine trades reproduce the label's
# r_long to <1e-6. The single outlier is the engine force-closing an open trade
# at end-of-data, which a label cannot know about. At horizon=60, 12/259 diverge
# — the engine has NO time limit, so the horizon must cover the realistic
# holding distribution (median 6 bars, p95 57, max 189) or slow winners get
# mislabelled as HOLD.
_HORIZON_BY_TF: dict[str, int] = {"1min": 360, "5min": 288, "15min": 240, "60min": 120}


def default_horizon(timeframe: str) -> int:
    return _HORIZON_BY_TF.get(timeframe, 240)


@dataclass(slots=True)
class LabelConfig:
    atr_stop_mult: float = 1.5
    rr: float = 2.0
    horizon: int = 240         # max bars before the trade is abandoned (see above)
    apply_costs: bool = True


@dataclass(slots=True)
class LabelResult:
    label: np.ndarray          # int8, one of SHORT/HOLD/LONG
    r_long: np.ndarray         # float64 net R if a LONG were taken at this bar
    r_short: np.ndarray        # float64 net R if a SHORT were taken
    valid: np.ndarray          # bool — enough forward data + non-degenerate stop
    exit_idx_long: np.ndarray  # int32 bar index the LONG resolved on (-1 = unresolved)
    exit_idx_short: np.ndarray


def _directional_r(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray,
    *, direction: int, cfg: LabelConfig, cost: CostModel,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Net R and resolution index for taking `direction` at every bar.

    Returns (r, exit_idx, valid). Unresolved trades (neither barrier touched
    within the horizon) get r=0 and exit_idx=-1 — they are labelled HOLD, not a
    win, because the engine would still be holding and we cannot claim a result.
    """
    n = len(c)
    r = np.zeros(n, dtype=np.float64)
    exit_idx = np.full(n, -1, dtype=np.int32)
    valid = np.zeros(n, dtype=bool)

    hs = float(cost.spread) / 2.0 if cfg.apply_costs else 0.0
    slip = float(cost.slippage) if cfg.apply_costs else 0.0
    comm_rate = (float(cost.commission_bps) / 10_000.0) * 2.0 if cfg.apply_costs else 0.0
    is_long = direction == LONG
    sign = 1.0 if is_long else -1.0

    for i in range(n - 1):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        risk = cfg.atr_stop_mult * a
        # The strategy prices BOTH stop and target off the signal bar's close
        # (see strategies/ml_xau.py), so the label must too.
        ref = c[i]
        stop = ref - risk if is_long else ref + risk
        target = ref + cfg.rr * risk if is_long else ref - cfg.rr * risk
        # The engine then fills on the NEXT bar's open plus half the spread, and
        # measures R against |fill - stop| — not against the strategy's nominal
        # risk. Slippage between close and next open is why realised RR drifts
        # slightly from the nominal `rr`.
        raw_entry = o[i + 1]
        entry = raw_entry + hs if is_long else raw_entry - hs
        risk_distance = abs(entry - stop)
        if risk_distance <= 0:
            continue

        j0 = i + 1
        j1 = min(n, j0 + cfg.horizon)
        if j1 <= j0:
            continue
        seg_h = h[j0:j1]
        seg_l = l[j0:j1]

        if is_long:
            stop_hits = seg_l <= stop
            tp_hits = seg_h >= target
        else:
            stop_hits = seg_h >= stop
            tp_hits = seg_l <= target

        any_stop = stop_hits.any()
        any_tp = tp_hits.any()
        if not any_stop and not any_tp:
            valid[i] = True          # a real, resolvable setup that simply timed out
            continue
        first_stop = int(np.argmax(stop_hits)) if any_stop else np.iinfo(np.int32).max
        first_tp = int(np.argmax(tp_hits)) if any_tp else np.iinfo(np.int32).max

        # Engine rule: both touched on the same bar -> the stop wins.
        hit_stop = first_stop <= first_tp
        k = first_stop if hit_stop else first_tp
        level = stop if hit_stop else target
        if is_long:
            exit_price = level - hs - (slip if hit_stop else 0.0)
        else:
            exit_price = level + hs + (slip if hit_stop else 0.0)

        gross = (exit_price - entry) * sign
        commission = entry * comm_rate          # per unit; size cancels in R
        r[i] = (gross - commission) / risk_distance
        exit_idx[i] = j0 + k
        valid[i] = True

    return r, exit_idx, valid


def triple_barrier(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray,
    *, cfg: LabelConfig | None = None, symbol: str = "XAUUSD",
) -> LabelResult:
    """Label every bar by simulating both a LONG and a SHORT from it.

    A bar is LONG only if the long trade actually nets positive R and beats the
    short, and vice versa. Everything else — chop where both stop out, and
    trades that never resolve — is HOLD. Deriving the class from *both* outcomes
    is what keeps the label set directionally balanced: upstream's one-sided
    `future_return > threshold` rule produced 51% SHORT on a bull market and a
    model that could only ever sell.
    """
    cfg = cfg or LabelConfig()
    cost = default_cost(symbol)

    r_long, xl, vl = _directional_r(o, h, l, c, atr, direction=LONG, cfg=cfg, cost=cost)
    r_short, xs, vs = _directional_r(o, h, l, c, atr, direction=SHORT, cfg=cfg, cost=cost)

    label = np.full(len(c), HOLD, dtype=np.int8)
    label[(r_long > 0) & (r_long > r_short)] = LONG
    label[(r_short > 0) & (r_short > r_long)] = SHORT

    return LabelResult(
        label=label, r_long=r_long, r_short=r_short,
        valid=vl & vs, exit_idx_long=xl, exit_idx_short=xs,
    )


def class_balance(label: np.ndarray, valid: np.ndarray | None = None) -> dict[str, float]:
    """Percentage split, used by the dataset builder's imbalance guard."""
    sel = label if valid is None else label[valid]
    n = max(1, len(sel))
    return {
        "SHORT": float((sel == SHORT).sum()) / n * 100.0,
        "HOLD": float((sel == HOLD).sum()) / n * 100.0,
        "LONG": float((sel == LONG).sum()) / n * 100.0,
        "n": float(len(sel)),
    }
