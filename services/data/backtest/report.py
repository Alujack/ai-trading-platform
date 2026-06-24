"""Human-readable rendering of backtest metrics + a blunt go/no-go verdict."""
from __future__ import annotations

from .metrics import Metrics

MIN_TRADES_FOR_CONFIDENCE = 30


def _pf(x: float) -> str:
    return "inf" if x == float("inf") else f"{x:.2f}"


def verdict(m: Metrics) -> str:
    """One-line, trader-blunt read on whether this result means anything."""
    if m.trades == 0:
        if m.signals_generated == 0:
            return "NO SETUPS — strategy never triggered on this data (check thresholds/scale)."
        return "NO TRADES — signals fired but none could be filled (insufficient bars)."
    if m.trades < MIN_TRADES_FOR_CONFIDENCE:
        base = f"NOT SIGNIFICANT — only {m.trades} trades; need ~{MIN_TRADES_FOR_CONFIDENCE}+ to trust any edge."
        return base
    if m.expectancy_r <= 0 or m.profit_factor < 1.0:
        return "NEGATIVE EDGE — loses money after costs. Do not deploy."
    if m.profit_factor < 1.2 or m.expectancy_r < 0.05:
        return "MARGINAL — barely positive; fragile to cost/slippage assumptions."
    return "POSITIVE (preliminary) — edge present on this sample; validate out-of-sample before sizing up."


def render_one(m: Metrics) -> str:
    lines = [
        f"── {m.strategy} · {m.symbol} · {m.timeframe} " + "─" * 24,
        f"  bars tested        {m.bars_tested:>10}   signals {m.signals_generated}",
        f"  trades             {m.trades:>10}   (W {m.wins} / L {m.losses} / BE {m.breakeven})"
        + (f"   [{m.eod_closed} closed at end-of-data]" if m.eod_closed else ""),
        f"  win rate           {m.win_rate * 100:>9.1f}%",
        f"  expectancy         {m.expectancy:>10.2f}  $/trade   ({m.expectancy_r:+.3f} R)",
        f"  profit factor      {_pf(m.profit_factor):>10}",
        f"  payoff (W:L)       {m.payoff_ratio:>10.2f}   avg win {m.avg_win:.2f} / avg loss {m.avg_loss:.2f}",
        f"  net P&L            {m.net_pnl:>10.2f}   ({m.return_pct * 100:+.1f}% on {m.starting_balance:.0f})",
        f"  max drawdown       {m.max_drawdown:>10.2f}   ({m.max_drawdown_pct * 100:.1f}%)",
        f"  max losing streak  {m.max_consecutive_losses:>10}",
        f"  avg hold (bars)    {m.avg_hold_bars:>10.1f}",
        f"  per-trade Sharpe   {m.sharpe_per_trade:>10.2f}",
        f"  >> {verdict(m)}",
    ]
    return "\n".join(lines)


def render_table(metrics: list[Metrics]) -> str:
    """Compact one-row-per-run comparison table."""
    header = (
        f"{'strategy':<13}{'symbol':<9}{'tf':<7}{'trades':>7}{'win%':>7}"
        f"{'exp$':>9}{'expR':>8}{'PF':>7}{'maxDD%':>8}{'net$':>11}"
    )
    sep = "─" * len(header)
    rows = [header, sep]
    for m in sorted(metrics, key=lambda x: (x.strategy, x.symbol, x.timeframe)):
        rows.append(
            f"{m.strategy:<13}{m.symbol:<9}{m.timeframe:<7}{m.trades:>7}"
            f"{m.win_rate * 100:>6.1f}%{m.expectancy:>9.2f}{m.expectancy_r:>+8.3f}"
            f"{_pf(m.profit_factor):>7}{m.max_drawdown_pct * 100:>7.1f}%{m.net_pnl:>11.2f}"
        )
    return "\n".join(rows)
