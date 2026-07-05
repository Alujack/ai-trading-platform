"""Pure bar-replay backtest engine.

No DB, no network — feed it a list of `Bar`s (ascending by time) and a strategy
instance, get back the list of simulated `Trade`s plus the equity curve.

Realism model (deliberately conservative — the goal is to *not* flatter a
strategy):

* Signal is computed on a fully-closed bar `i`; entry fills at the **next** bar's
  open (`i+1`). No same-bar look-ahead.
* Exits are checked **intrabar** against each later bar's high/low — a real stop
  or target can be touched without the close confirming it.
* When a single bar's range spans BOTH the stop and the target, we assume the
  **stop** was hit first (worst case — OHLC alone can't tell the order).
* Spread, slippage, and commission are charged on every trade. Entry fills at the
  adverse side of the spread; stop exits eat slippage on top.
* Position size comes from the same risk-per-trade rule the live engine uses
  (`riskAmount / stopDistance`), but on **compounding** equity so the curve is
  realistic rather than frozen at a static balance.

Each strategy in this repo is a pure function of a single bar's indicators (none
look across bars), so we evaluate against a one-bar window per step — which also
guarantees exactly one decision per bar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Sequence

from strategies import BarWindow, IndicatorBar
from strategies.base import Strategy

ZERO = Decimal("0")
TWO = Decimal("2")


@dataclass(slots=True)
class Bar:
    """One OHLCV bar plus its (causal) indicator readings. Any indicator may be
    None until enough warmup history exists — strategies guard against that."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    rsi: Decimal | None = None
    ema20: Decimal | None = None
    ema50: Decimal | None = None
    ema200: Decimal | None = None
    atr: Decimal | None = None
    bb_lower: Decimal | None = None
    bb_upper: Decimal | None = None
    bb_pctb: Decimal | None = None
    adx: Decimal | None = None
    volume: Decimal | None = None  # needed by volume-aware strategies (e.g. scalp_vwap's VWAP)

    def to_indicator_bar(self) -> IndicatorBar:
        return IndicatorBar(
            timestamp=self.timestamp,
            close=self.close,
            rsi=self.rsi,
            ema20=self.ema20,
            ema50=self.ema50,
            ema200=self.ema200,
            atr=self.atr,
            bb_lower=self.bb_lower,
            bb_upper=self.bb_upper,
            bb_pctb=self.bb_pctb,
            adx=self.adx,
            open=self.open,
            high=self.high,
            low=self.low,
            volume=self.volume,
        )


@dataclass(slots=True)
class CostModel:
    """Per-trade transaction costs, in price units (spread/slippage) and basis
    points of notional (commission). Defaults are *retail estimates* — calibrate
    them to your broker before trusting absolute P&L."""

    spread: Decimal = ZERO            # full bid/ask spread in price units
    slippage: Decimal = ZERO          # extra adverse fill on STOP exits, price units
    commission_bps: Decimal = ZERO    # per side, basis points of notional

    def half_spread(self) -> Decimal:
        return self.spread / TWO

    def commission(self, notional: Decimal) -> Decimal:
        # Charged on both legs (round turn).
        return notional * (self.commission_bps / Decimal("10000")) * TWO


# Sensible, conservative retail defaults per symbol. Spread-dominated, but
# commission is now NON-ZERO because a zero-commission backtest flatters
# high-frequency strategies badly (a 4k-trade scalper pays nothing in the model
# yet bleeds real fees live). Values are per-side basis points of notional:
#   - BTCUSD 7.5 bps/side ≈ a typical crypto taker fee (~0.075%); round turn 15 bps.
#   - XAUUSD / EURUSD 0.4 bps/side ≈ an ECN-style commission (~$3.5 per $100k/side)
#     on top of the modeled spread. Spread-only retail accounts can set this to 0.
# All are ESTIMATES — override with --commission-bps (or --no-costs) to calibrate.
DEFAULT_COSTS: dict[str, CostModel] = {
    "XAUUSD": CostModel(spread=Decimal("0.30"), slippage=Decimal("0.10"), commission_bps=Decimal("0.4")),
    "EURUSD": CostModel(spread=Decimal("0.00012"), slippage=Decimal("0.00003"), commission_bps=Decimal("0.4")),
    "BTCUSD": CostModel(spread=Decimal("12"), slippage=Decimal("5"), commission_bps=Decimal("7.5")),
}


def default_cost(symbol: str) -> CostModel:
    return DEFAULT_COSTS.get(symbol, CostModel())


@dataclass(slots=True)
class BacktestConfig:
    starting_balance: Decimal = Decimal("10000")
    risk_pct: Decimal = Decimal("1")          # risk per trade, % of current equity
    cost: CostModel = field(default_factory=CostModel)
    apply_costs: bool = True
    stop_first_on_ambiguous: bool = True       # both touched same bar -> stop wins
    # Gate candidates by market regime, mirroring the live runner
    # (strategy_runner.py). Default True so the backtest evaluates the SAME
    # system that trades live; set False to measure the un-gated strategy.
    regime_gating: bool = True


@dataclass(slots=True)
class Trade:
    symbol: str
    timeframe: str
    strategy: str
    direction: str                 # "LONG" | "SHORT"
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal           # actual fill, after spread
    stop: Decimal
    target: Decimal
    exit_price: Decimal            # actual fill, after spread/slippage
    size: Decimal
    risk_amount: Decimal
    gross_pnl: Decimal
    commission: Decimal
    net_pnl: Decimal
    r_multiple: Decimal            # net_pnl / risk_amount
    exit_reason: str               # "target" | "stop" | "eod"
    hold_bars: int
    equity_after: Decimal


@dataclass(slots=True)
class RunResult:
    symbol: str
    timeframe: str
    strategy: str
    trades: list[Trade]
    equity_curve: list[tuple[datetime, Decimal]]
    starting_balance: Decimal
    ending_balance: Decimal
    bars_tested: int
    signals_generated: int          # candidates produced (pre single-position gating)
    skipped_no_next_bar: int
    regime_gated: int = 0           # candidates suppressed because the live regime
                                    # isn't in the strategy's allowed set


def _dir_sign(direction: str) -> Decimal:
    return Decimal("1") if direction == "LONG" else Decimal("-1")


def _detect_exit(
    direction: str,
    bar: Bar,
    stop: Decimal,
    target: Decimal,
    stop_first: bool,
) -> str | None:
    """Return 'stop', 'target', or None for an intrabar touch on this bar."""
    if direction == "LONG":
        stop_hit = bar.low <= stop
        tp_hit = bar.high >= target
    else:  # SHORT
        stop_hit = bar.high >= stop
        tp_hit = bar.low <= target

    if stop_hit and tp_hit:
        return "stop" if stop_first else "target"
    if stop_hit:
        return "stop"
    if tp_hit:
        return "target"
    return None


def _entry_fill(direction: str, raw: Decimal, cost: CostModel, apply: bool) -> Decimal:
    if not apply:
        return raw
    hs = cost.half_spread()
    return raw + hs if direction == "LONG" else raw - hs


def _exit_fill(
    direction: str, level: Decimal, reason: str, cost: CostModel, apply: bool
) -> Decimal:
    if not apply:
        return level
    hs = cost.half_spread()
    slip = cost.slippage if reason == "stop" else ZERO
    if direction == "LONG":
        # We sell: receive bid (level - half spread), minus slippage on stops.
        return level - hs - slip
    # SHORT: we buy back at ask (level + half spread), plus slippage on stops.
    return level + hs + slip


def simulate(
    strategy: Strategy,
    bars: Sequence[Bar],
    symbol: str,
    timeframe: str,
    config: BacktestConfig,
) -> RunResult:
    """Replay `bars` (ascending by time) through `strategy` and simulate trades.

    Holds at most one open position at a time (mirrors the live cooldown /
    max-open behaviour for a single strategy+symbol), and honours each
    candidate's `cooldown_ms` after a trade closes.
    """
    equity = config.starting_balance
    cost = config.cost
    apply_costs = config.apply_costs

    trades: list[Trade] = []
    equity_curve: list[tuple[datetime, Decimal]] = []
    signals = 0
    skipped_no_next = 0
    regime_gated = 0

    # The same regime classifier the live runner uses. Imported lazily so the
    # engine has no hard dependency on pandas/pandas_ta unless gating is on.
    if config.regime_gating:
        from regime import REGIME_LOOKBACK_BARS, UNKNOWN, compute_regime

        allowed_regimes = getattr(strategy, "regimes", set())

    # How many trailing bars to hand the strategy each step. Close-only strategies
    # use 1 (identical to the old single-bar behaviour); multi-bar price-action
    # detectors (ICT) declare a larger `lookback`. The window is sliced from
    # ALREADY-CLOSED bars (… , i-1, i) and never includes future bars, so there is
    # no look-ahead — entry still fills on bar i+1's open below.
    lookback = max(1, int(getattr(strategy, "lookback", 1)))

    open_dir: str | None = None
    entry_idx = 0
    entry_price = ZERO
    stop = ZERO
    target = ZERO
    size = ZERO
    risk_amount = ZERO
    signal_time: datetime | None = None
    cooldown_ms: int | None = None
    last_exit_time: datetime | None = None
    last_cooldown_ms: int | None = None

    n = len(bars)
    for i in range(n):
        bar = bars[i]

        # 1) Manage an open position: check for an intrabar exit on this bar.
        if open_dir is not None and i >= entry_idx:
            reason = _detect_exit(open_dir, bar, stop, target, config.stop_first_on_ambiguous)
            if reason is not None:
                level = stop if reason == "stop" else target
                exit_price = _exit_fill(open_dir, level, reason, cost, apply_costs)
                sign = _dir_sign(open_dir)
                gross = (exit_price - entry_price) * size * sign
                notional = entry_price * size
                commission = cost.commission(notional) if apply_costs else ZERO
                net = gross - commission
                equity += net
                trades.append(
                    Trade(
                        symbol=symbol,
                        timeframe=timeframe,
                        strategy=strategy.name,
                        direction=open_dir,
                        signal_time=signal_time,  # type: ignore[arg-type]
                        entry_time=bars[entry_idx].timestamp,
                        exit_time=bar.timestamp,
                        entry_price=entry_price,
                        stop=stop,
                        target=target,
                        exit_price=exit_price,
                        size=size,
                        risk_amount=risk_amount,
                        gross_pnl=gross,
                        commission=commission,
                        net_pnl=net,
                        r_multiple=(net / risk_amount) if risk_amount > ZERO else ZERO,
                        exit_reason=reason,
                        hold_bars=i - entry_idx,
                        equity_after=equity,
                    )
                )
                equity_curve.append((bar.timestamp, equity))
                last_exit_time = bar.timestamp
                last_cooldown_ms = cooldown_ms
                open_dir = None
                # fall through: do not also open on the same bar (one action/bar).
                continue

        # 2) Flat: look for a new entry signal on this (closed) bar.
        if open_dir is None:
            if last_exit_time is not None and last_cooldown_ms:
                if bar.timestamp - last_exit_time < timedelta(milliseconds=last_cooldown_ms):
                    continue
            # Trailing window ending at this closed bar, most-recent-first to
            # match the live runner's contract (bars[0] == latest).
            lo = max(0, i - lookback + 1)
            win_bars = [bars[j].to_indicator_bar() for j in range(i, lo - 1, -1)]
            window = BarWindow(symbol=symbol, timeframe=timeframe, bars=win_bars)
            candidates = strategy.evaluate(window)
            if not candidates:
                continue
            signals += 1
            cand = candidates[0]

            # Regime gate — mirror the live runner (strategy_runner.py): classify
            # the regime from the causal window ENDING at this signal bar, and
            # skip the candidate when the strategy doesn't trade that regime.
            # UNKNOWN fails open (don't suppress on thin/early data), exactly as
            # live. Computed lazily here so it only runs when a candidate exists.
            if config.regime_gating:
                lo = max(0, i - REGIME_LOOKBACK_BARS + 1)
                hl = bars[lo : i + 1]
                reading = compute_regime(
                    [b.high for b in hl], [b.low for b in hl], [b.close for b in hl]
                )
                if reading.regime != UNKNOWN and reading.regime not in allowed_regimes:
                    regime_gated += 1
                    continue

            if i + 1 >= n:
                skipped_no_next += 1
                continue  # no next bar to fill the entry on

            fill_bar = bars[i + 1]
            raw_entry = fill_bar.open
            ep = _entry_fill(cand.direction, raw_entry, cost, apply_costs)
            risk_distance = abs(ep - cand.stop)
            if risk_distance <= ZERO:
                continue  # degenerate stop; skip

            risk_amount = equity * (config.risk_pct / Decimal("100"))
            size = risk_amount / risk_distance

            open_dir = cand.direction
            entry_idx = i + 1
            entry_price = ep
            stop = cand.stop
            target = cand.target
            signal_time = bar.timestamp
            cooldown_ms = cand.cooldown_ms

    # 3) Force-close any position still open at the end of the data (mark-to-market
    #    at the last bar's close), so the equity curve is complete.
    if open_dir is not None and n > 0:
        last = bars[-1]
        exit_price = _exit_fill(open_dir, last.close, "eod", cost, apply_costs)
        sign = _dir_sign(open_dir)
        gross = (exit_price - entry_price) * size * sign
        notional = entry_price * size
        commission = cost.commission(notional) if apply_costs else ZERO
        net = gross - commission
        equity += net
        trades.append(
            Trade(
                symbol=symbol,
                timeframe=timeframe,
                strategy=strategy.name,
                direction=open_dir,
                signal_time=signal_time,  # type: ignore[arg-type]
                entry_time=bars[entry_idx].timestamp,
                exit_time=last.timestamp,
                entry_price=entry_price,
                stop=stop,
                target=target,
                exit_price=exit_price,
                size=size,
                risk_amount=risk_amount,
                gross_pnl=gross,
                commission=commission,
                net_pnl=net,
                r_multiple=(net / risk_amount) if risk_amount > ZERO else ZERO,
                exit_reason="eod",
                hold_bars=(n - 1) - entry_idx,
                equity_after=equity,
            )
        )
        equity_curve.append((last.timestamp, equity))

    return RunResult(
        symbol=symbol,
        timeframe=timeframe,
        strategy=strategy.name,
        trades=trades,
        equity_curve=equity_curve,
        starting_balance=config.starting_balance,
        ending_balance=equity,
        bars_tested=n,
        signals_generated=signals,
        skipped_no_next_bar=skipped_no_next,
        regime_gated=regime_gated,
    )
