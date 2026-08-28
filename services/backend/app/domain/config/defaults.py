"""Code-level defaults and hard bounds for every runtime-configurable risk field.

Port of `apps/api/src/config/defaults.ts`. These are the seed/fallback values: a
resolved field falls through SYMBOL ► STRATEGY ► GLOBAL and finally lands here.
The env-var reads are preserved so an operator's existing `.env` behaves
identically after the migration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Literal

from ...core.settings import get_settings

Scope = Literal["GLOBAL", "STRATEGY", "SYMBOL"]
SCOPES: tuple[Scope, ...] = ("GLOBAL", "STRATEGY", "SYMBOL")


@dataclass(slots=True)
class EffectiveRiskConfig:
    """The fully-resolved risk parameters for one (strategy, symbol) context."""

    riskPerTradePct: float
    minRR: float
    dailyLossLimitPct: float
    dailyProfitTargetPct: float
    maxDrawdownPct: float
    maxOpenTrades: float
    maxTradesPerDay: float
    maxOpenRiskPct: float
    maxRiskPerCurrencyPct: float
    newsBeforeMin: float
    newsAfterMin: float
    aiMinScore: float
    approvalTtlMin: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Bound:
    min: float
    max: float
    int: bool = False


def risk_defaults() -> EffectiveRiskConfig:
    """Defaults, re-read from settings so tests can vary the environment."""
    cfg = get_settings()
    return EffectiveRiskConfig(
        riskPerTradePct=cfg.paper_risk_percent,
        minRR=2,
        dailyLossLimitPct=3,
        # Sticky rule: bank the green day. Once today's realized P&L reaches this
        # % of balance, the breaker holds all new trades until the next UTC day.
        dailyProfitTargetPct=cfg.daily_profit_target_pct,
        maxDrawdownPct=10,
        # Sticky rule: one trade at a time. Risk 1% to make 2%.
        maxOpenTrades=cfg.paper_max_open_trades,
        # Sticky rule: ONE trade per day, then stop.
        maxTradesPerDay=cfg.max_trades_per_day,
        maxOpenRiskPct=5,
        # All instruments here are USD-quoted, so a tight per-currency cap
        # throttles everything at once. Keep it level with the open-risk cap.
        maxRiskPerCurrencyPct=5,
        newsBeforeMin=30,
        newsAfterMin=30,
        aiMinScore=70,
        approvalTtlMin=15,
    )


#: Hard bounds the API enforces so the UI can never set a self-destructive value.
RISK_BOUNDS: dict[str, Bound] = {
    "riskPerTradePct": Bound(0.01, 5),
    "minRR": Bound(1, 10),
    "dailyLossLimitPct": Bound(0.1, 50),
    "dailyProfitTargetPct": Bound(0.1, 100),
    "maxDrawdownPct": Bound(0.1, 100),
    "maxOpenTrades": Bound(1, 100, int=True),
    "maxTradesPerDay": Bound(1, 100, int=True),
    "maxOpenRiskPct": Bound(0.1, 100),
    "maxRiskPerCurrencyPct": Bound(0.1, 100),
    "newsBeforeMin": Bound(0, 240, int=True),
    "newsAfterMin": Bound(0, 240, int=True),
    "aiMinScore": Bound(0, 100, int=True),
    "approvalTtlMin": Bound(1, 1440, int=True),
}

#: Field order used when layering scopes — matches RISK_FIELDS in resolve.ts.
RISK_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(EffectiveRiskConfig))

#: Static currency map for per-currency exposure caps (Part B §3.4.3).
SYMBOL_CURRENCIES: dict[str, list[str]] = {
    "XAUUSD": ["XAU", "USD"],
    "EURUSD": ["EUR", "USD"],
    "BTCUSD": ["BTC", "USD"],
}


def bounds_wire() -> dict[str, dict[str, float | bool]]:
    """`RISK_BOUNDS` in the JSON shape the dashboard already renders."""
    out: dict[str, dict[str, float | bool]] = {}
    for key, b in RISK_BOUNDS.items():
        entry: dict[str, float | bool] = {"min": b.min, "max": b.max}
        if b.int:
            entry["int"] = True
        out[key] = entry
    return out
