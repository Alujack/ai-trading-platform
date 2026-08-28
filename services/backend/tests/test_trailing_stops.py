"""Trailing-stop decision core — behaviour ported from `trailingStopManager.ts`."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domain.execution.trailing_stops import (
    DEFAULT_TRAILING_CONFIG,
    TrailingStopInput,
    evaluate_trailing_stop,
    with_overrides,
)

OPENED = datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc)


def inp(**over) -> TrailingStopInput:
    base = {
        "entryPrice": 2650.0,
        "currentPrice": 2650.0,
        "currentStop": 2640.0,
        "direction": "LONG",
        "atr": 12.5,
        "openedAt": OPENED,
        "now": OPENED + timedelta(minutes=10),
    }
    base.update(over)
    return TrailingStopInput(**base)  # type: ignore[arg-type]


class TestTimeExit:
    def test_exits_once_the_max_hold_is_reached(self):
        action = evaluate_trailing_stop(inp(now=OPENED + timedelta(minutes=240)))
        assert action.type == "TIME_EXIT"
        assert "240min limit" in action.reason

    def test_time_exit_takes_precedence_over_a_profitable_trail(self):
        action = evaluate_trailing_stop(
            inp(currentPrice=2700.0, now=OPENED + timedelta(minutes=300))
        )
        assert action.type == "TIME_EXIT"

    def test_holds_just_below_the_max_hold(self):
        action = evaluate_trailing_stop(inp(now=OPENED + timedelta(minutes=239)))
        assert action.type != "TIME_EXIT"


class TestPartialClose:
    def test_scales_out_half_at_one_atr_and_moves_the_stop_to_breakeven(self):
        # 1×ATR = 12.5 above entry.
        action = evaluate_trailing_stop(inp(currentPrice=2662.5, positionSize=1.0))
        assert action.type == "PARTIAL_CLOSE"
        assert action.closeSize == 0.5
        assert action.newStop == 2650.0 + DEFAULT_TRAILING_CONFIG.breakevenSpread

    def test_does_not_repeat_once_the_partial_was_taken(self):
        action = evaluate_trailing_stop(
            inp(currentPrice=2662.5, positionSize=1.0, partialTaken=True)
        )
        assert action.type != "PARTIAL_CLOSE"

    def test_is_skipped_when_the_position_size_is_unknown(self):
        action = evaluate_trailing_stop(inp(currentPrice=2662.5))
        assert action.type != "PARTIAL_CLOSE"


class TestTrail:
    def test_trails_behind_price_once_one_atr_in_profit(self):
        # profit = 1.2×ATR; trail sits 1.5×ATR = 18.75 behind 2665.
        action = evaluate_trailing_stop(inp(currentPrice=2665.0, partialTaken=True))
        assert action.type == "TRAIL"
        assert action.newStop == 2646.25

    def test_never_widens_an_existing_stop(self):
        # A stop already tighter than the computed trail must not be moved back.
        action = evaluate_trailing_stop(
            inp(currentPrice=2665.0, currentStop=2660.0, partialTaken=True)
        )
        assert action.type != "TRAIL"

    def test_trails_the_other_way_for_a_short(self):
        action = evaluate_trailing_stop(
            inp(
                direction="SHORT",
                currentPrice=2635.0,
                currentStop=2660.0,
                partialTaken=True,
            )
        )
        assert action.type == "TRAIL"
        assert action.newStop == 2653.75  # 2635 + 1.5×12.5


class TestBreakeven:
    def test_locks_breakeven_at_half_an_atr(self):
        # 0.5×ATR = 6.25 above entry, below the 1×ATR trail trigger.
        action = evaluate_trailing_stop(inp(currentPrice=2657.0))
        assert action.type == "BREAKEVEN"
        assert action.newStop == 2650.2

    def test_does_not_re_lock_once_the_stop_is_past_entry(self):
        action = evaluate_trailing_stop(inp(currentPrice=2657.0, currentStop=2651.0))
        assert action.type == "HOLD"

    def test_locks_the_other_way_for_a_short(self):
        action = evaluate_trailing_stop(
            inp(direction="SHORT", currentPrice=2643.0, currentStop=2660.0)
        )
        assert action.type == "BREAKEVEN"
        assert action.newStop == 2649.8


class TestHold:
    def test_holds_below_every_trigger(self):
        action = evaluate_trailing_stop(inp(currentPrice=2652.0))
        assert action.type == "HOLD"
        assert "below trigger thresholds" in action.reason

    def test_holds_when_the_atr_is_unusable(self):
        # atr = 0 would divide by zero; profit in ATR units is treated as 0.
        action = evaluate_trailing_stop(inp(currentPrice=2700.0, atr=0))
        assert action.type == "HOLD"

    def test_treats_a_naive_opened_at_as_utc(self):
        # `Trade.openedAt` comes back from the DB naive; reading it as local time
        # would mis-measure the hold duration by the machine's offset.
        action = evaluate_trailing_stop(
            inp(openedAt=datetime(2026, 8, 28, 14, 0), now=OPENED + timedelta(minutes=10))
        )
        assert action.type == "HOLD"


class TestWithOverrides:
    def test_merges_a_partial_config_over_the_defaults(self):
        cfg = with_overrides(maxHoldMinutes=60)
        assert cfg.maxHoldMinutes == 60
        assert cfg.trailDistanceAtr == DEFAULT_TRAILING_CONFIG.trailDistanceAtr

    def test_a_custom_config_is_honoured(self):
        action = evaluate_trailing_stop(
            inp(now=OPENED + timedelta(minutes=61), config=with_overrides(maxHoldMinutes=60))
        )
        assert action.type == "TIME_EXIT"
