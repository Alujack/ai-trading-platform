"""Performance-metric parity, translated from `performance.test.ts`."""
from __future__ import annotations

import math
from dataclasses import replace

from app.domain.performance.metrics import TradeStats, compute_performance

WIN_LONG = TradeStats(entryPrice=100, exitPrice=104, profitLoss=20, direction="LONG", stopLoss=98)
LOSS_LONG = TradeStats(entryPrice=100, exitPrice=98, profitLoss=-10, direction="LONG", stopLoss=98)
WIN_SHORT = TradeStats(entryPrice=100, exitPrice=96, profitLoss=20, direction="SHORT", stopLoss=102)


def test_returns_all_zeros_for_an_empty_trade_list():
    assert compute_performance([]) == {
        "totalTrades": 0,
        "winRate": 0,
        "totalPnL": 0,
        "maxDrawdown": 0,
        "averageRR": 0,
        "expectancy": 0,
        "profitFactor": 0,
    }


def test_computes_expectancy_and_profit_factor():
    # winLong +20, lossLong -10 → expectancy (20-10)/2 = 5; PF 20/10 = 2
    result = compute_performance([WIN_LONG, LOSS_LONG])
    assert result["expectancy"] == 5
    assert result["profitFactor"] == 2


def test_reports_infinite_profit_factor_with_no_losses():
    assert compute_performance([WIN_LONG])["profitFactor"] == math.inf


def test_computes_win_rate_pnl_and_rr_for_a_single_winning_long():
    result = compute_performance([WIN_LONG])
    assert result["totalTrades"] == 1
    assert result["winRate"] == 100
    assert result["totalPnL"] == 20
    assert result["averageRR"] == 2  # (104-100)/(100-98)
    assert result["maxDrawdown"] == 0  # no trough after the peak


def test_computes_a_negative_rr_for_a_full_risk_loss():
    result = compute_performance([LOSS_LONG])
    assert result["winRate"] == 0
    assert result["averageRR"] == -1  # exit hit SL exactly


def test_mirrors_rr_sign_for_shorts():
    assert compute_performance([WIN_SHORT])["averageRR"] == 2  # (100-96)/|100-102|


def test_averages_rr_across_mixed_trades():
    result = compute_performance([WIN_LONG, LOSS_LONG])
    assert result["averageRR"] == 0.5  # (2 + -1) / 2
    assert result["winRate"] == 50
    assert result["totalPnL"] == 10


def test_max_drawdown_is_the_largest_peak_to_trough_drop():
    # Cumulative: 20, 10, -5, 15 → peak 20, trough -5 → drawdown 25
    seq = [
        replace(WIN_LONG, profitLoss=20),
        replace(LOSS_LONG, profitLoss=-10),
        replace(LOSS_LONG, profitLoss=-15),
        replace(WIN_LONG, profitLoss=20),
    ]
    result = compute_performance(seq)
    assert result["totalPnL"] == 15
    assert result["maxDrawdown"] == 25


def test_ignores_rr_from_trades_with_no_exit_price():
    still_open = replace(WIN_LONG, exitPrice=None, profitLoss=0)
    assert compute_performance([still_open, WIN_LONG])["averageRR"] == 2


def test_ignores_rr_when_risk_is_zero():
    degenerate = TradeStats(
        entryPrice=100, exitPrice=105, profitLoss=5, direction="LONG", stopLoss=100
    )
    assert compute_performance([degenerate])["averageRR"] == 0


def test_treats_null_profit_loss_as_zero():
    no_pnl = replace(WIN_LONG, profitLoss=None)
    result = compute_performance([no_pnl])
    assert result["totalPnL"] == 0
    assert result["winRate"] == 0  # null P&L doesn't count as a win
