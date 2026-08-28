"""Risk-engine parity tests, translated case-for-case from `riskEngine.test.ts`.

These are the boundary cases the plan's risk matrix calls out: position size at
valid/invalid inputs, the exact minimum RR tolerance, daily-loss equality versus
greater-than, and both sides of the high-impact news window.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.domain.risk.engine import (
    NewsLite,
    calculate_position_size,
    check_daily_loss,
    check_max_drawdown,
    is_news_window,
    validate_risk_reward,
)


def at(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


class TestCalculatePositionSize:
    def test_lot_size_is_risk_amount_over_stop_distance(self):
        sized = calculate_position_size(10_000, 1, 100, 98)
        assert sized.riskAmount == 100
        assert sized.lotSize == 50

    def test_uses_absolute_distance_regardless_of_stop_direction(self):
        long = calculate_position_size(10_000, 1, 100, 98)
        short = calculate_position_size(10_000, 1, 100, 102)
        assert short.lotSize == long.lotSize

    def test_scales_with_risk_percent(self):
        one_pct = calculate_position_size(10_000, 1, 100, 95)
        two_pct = calculate_position_size(10_000, 2, 100, 95)
        assert two_pct.lotSize == pytest.approx(one_pct.lotSize * 2, abs=1e-10)

    def test_raises_when_entry_equals_stop_loss(self):
        with pytest.raises(ValueError):
            calculate_position_size(10_000, 1, 100, 100)

    def test_raises_on_non_positive_balance(self):
        with pytest.raises(ValueError):
            calculate_position_size(0, 1, 100, 98)
        with pytest.raises(ValueError):
            calculate_position_size(-1, 1, 100, 98)

    def test_raises_on_non_positive_risk_percent(self):
        with pytest.raises(ValueError):
            calculate_position_size(10_000, 0, 100, 98)


class TestCheckDailyLoss:
    def test_allows_when_loss_is_below_the_limit(self):
        assert check_daily_loss("u1", 100, 10_000).allowed is True

    def test_allows_when_loss_is_exactly_the_limit(self):
        # Per spec wording "if todayLoss > 3% of balance: not allowed".
        result = check_daily_loss("u1", 300, 10_000)
        assert result.allowed is True

    def test_blocks_when_loss_exceeds_the_limit(self):
        result = check_daily_loss("u1", 301, 10_000)
        assert result.allowed is False
        assert result.reason == "Daily loss limit reached"

    def test_respects_a_custom_limit_percent(self):
        blocked = check_daily_loss("u1", 200, 10_000, 1)
        assert (blocked.allowed, blocked.reason) == (False, "Daily loss limit reached")
        assert check_daily_loss("u1", 99, 10_000, 1).allowed is True


class TestCheckMaxDrawdown:
    def test_allows_below_the_limit(self):
        assert check_max_drawdown(10_000, 9_500).allowed is True

    def test_allows_at_exactly_the_limit(self):
        assert check_max_drawdown(10_000, 9_000).allowed is True

    def test_blocks_above_the_limit(self):
        result = check_max_drawdown(10_000, 8_999)
        assert result.allowed is False
        assert result.reason == "Max drawdown exceeded"

    def test_rejects_an_invalid_peak_balance(self):
        assert check_max_drawdown(0, 1_000).allowed is False
        assert check_max_drawdown(-1, 1_000).allowed is False


class TestValidateRiskReward:
    def test_accepts_a_clean_1_to_2_long(self):
        result = validate_risk_reward(100, 98, 104)
        assert result.rr == 2
        assert result.acceptable is True

    def test_accepts_a_clean_1_to_2_short(self):
        result = validate_risk_reward(100, 102, 96)
        assert result.rr == 2
        assert result.acceptable is True

    def test_rejects_rr_just_below_the_threshold(self):
        assert validate_risk_reward(100, 98, 103.99).acceptable is False

    def test_accepts_exact_1_to_2_despite_float_rounding(self):
        # 50-pip stop / 100-pip target on EURUSD: 0.0100 / 0.0050 rounds just
        # under 2.0 in binary floating point.
        result = validate_risk_reward(1.1, 1.095, 1.11)
        assert result.rr < 2  # float rounds just under
        assert result.acceptable is True  # ...the epsilon-tolerant gate accepts it

    def test_handles_zero_risk_gracefully(self):
        result = validate_risk_reward(100, 100, 104)
        assert (result.rr, result.acceptable) == (0, False)

    def test_respects_a_custom_min_rr(self):
        assert validate_risk_reward(100, 99, 102, 3).acceptable is False
        assert validate_risk_reward(100, 99, 103, 3).acceptable is True


class TestIsNewsWindow:
    NOW = at("2026-05-17T12:00:00Z")

    def test_safe_with_no_events(self):
        result = is_news_window([], 30, 30, self.NOW)
        assert (result.safe, result.nearestEvent) == (True, None)

    def test_ignores_low_and_medium_impact_even_when_near(self):
        news = [
            NewsLite("Minor", "LOW", at("2026-05-17T12:05:00Z")),
            NewsLite("Mid", "MEDIUM", at("2026-05-17T12:10:00Z")),
        ]
        result = is_news_window(news, 30, 30, self.NOW)
        assert result.safe is True
        assert result.nearestEvent is None

    def test_flags_a_high_impact_event_15_min_ahead(self):
        news = [NewsLite("CPI", "HIGH", at("2026-05-17T12:15:00Z"))]
        result = is_news_window(news, 30, 30, self.NOW)
        assert result.safe is False
        assert result.nearestEvent == "CPI"

    def test_event_just_outside_the_window_is_safe_but_still_reported(self):
        news = [NewsLite("NFP", "HIGH", at("2026-05-17T12:31:00Z"))]
        result = is_news_window(news, 30, 30, self.NOW)
        assert result.safe is True
        assert result.nearestEvent == "NFP"

    def test_flags_an_event_20_min_in_the_past(self):
        news = [NewsLite("FOMC", "HIGH", at("2026-05-17T11:40:00Z"))]
        assert is_news_window(news, 30, 30, self.NOW).safe is False

    def test_event_31_min_in_the_past_is_safe(self):
        news = [NewsLite("Old", "HIGH", at("2026-05-17T11:29:00Z"))]
        assert is_news_window(news, 30, 30, self.NOW).safe is True

    def test_reports_the_nearest_event_across_several(self):
        news = [
            NewsLite("Far", "HIGH", at("2026-05-17T14:00:00Z")),
            NewsLite("Near", "HIGH", at("2026-05-17T12:45:00Z")),
        ]
        assert is_news_window(news, 30, 30, self.NOW).nearestEvent == "Near"

    def test_treats_a_naive_timestamp_as_utc(self):
        # The `TIMESTAMP(3)` columns come back naive; they must not be read as local time.
        news = [NewsLite("CPI", "HIGH", datetime(2026, 5, 17, 12, 15))]
        assert is_news_window(news, 30, 30, self.NOW).safe is False

    def test_window_bounds_are_inclusive(self):
        exactly_before = [NewsLite("Edge", "HIGH", at("2026-05-17T12:30:00Z"))]
        exactly_after = [NewsLite("Edge", "HIGH", at("2026-05-17T11:30:00Z"))]
        assert is_news_window(exactly_before, 30, 30, self.NOW).safe is False
        assert is_news_window(exactly_after, 30, 30, self.NOW).safe is False
