"""Scalp-management parity, translated from `scalpManager.test.ts`."""
from __future__ import annotations

from app.domain.execution.scalp_decision import (
    ScalpDecisionInput,
    ScalpManageConfig,
    TicketState,
    decide_scalp_action,
    load_config,
)

CFG = ScalpManageConfig(
    minStopRatio=0.5, emergencyR=0.8, watchR=0.5, trailStartR=1.0, trailGivebackR=0.5
)

# Safe, non-triggering stop distances for cases not about slippage.
SAFE = {"intendedStopDist": 10, "actualStopDist": 10}


class TestUnsafeStop:
    def test_closes_on_first_sight_when_the_fill_ate_half_the_stop_room(self):
        decision = decide_scalp_action(
            ScalpDecisionInput(state=None, r=0, intendedStopDist=10, actualStopDist=3), CFG
        )
        assert decision.action == "close"
        assert decision.reason == "unsafe_stop_slippage"

    def test_does_not_retrigger_after_the_first_check(self):
        state = TicketState(checks=1, lastR=0, bestR=0)
        decision = decide_scalp_action(
            ScalpDecisionInput(state=state, r=0, intendedStopDist=10, actualStopDist=3), CFG
        )
        assert decision.action == "hold"

    def test_holds_on_first_sight_when_the_stop_survived_the_fill(self):
        decision = decide_scalp_action(
            ScalpDecisionInput(state=None, r=-0.2, intendedStopDist=10, actualStopDist=9), CFG
        )
        assert decision.action == "hold"
        assert decision.nextState == TicketState(checks=1, lastR=-0.2, bestR=-0.2)


class TestAdverse:
    def test_emergency_closes_in_a_single_check(self):
        decision = decide_scalp_action(ScalpDecisionInput(state=None, r=-0.9, **SAFE), CFG)
        assert decision.action == "close"
        assert decision.reason == "emergency_adverse"

    def test_two_check_closes_when_worse_and_inside_the_watch_band(self):
        state = TicketState(checks=1, lastR=-0.4, bestR=0)
        decision = decide_scalp_action(ScalpDecisionInput(state=state, r=-0.6, **SAFE), CFG)
        assert decision.action == "close"
        assert decision.reason == "two_check_adverse"

    def test_does_not_two_check_close_on_recovery(self):
        state = TicketState(checks=1, lastR=-0.6, bestR=-0.2)
        decision = decide_scalp_action(ScalpDecisionInput(state=state, r=-0.4, **SAFE), CFG)
        assert decision.action == "hold"

    def test_does_not_two_check_close_above_the_watch_band(self):
        state = TicketState(checks=1, lastR=-0.2, bestR=0)
        decision = decide_scalp_action(ScalpDecisionInput(state=state, r=-0.4, **SAFE), CFG)
        assert decision.action == "hold"


class TestProfitLock:
    def test_locks_profit_once_armed_and_price_gives_back(self):
        state = TicketState(checks=3, lastR=1.2, bestR=1.2)
        decision = decide_scalp_action(ScalpDecisionInput(state=state, r=0.6, **SAFE), CFG)
        assert decision.action == "close"
        assert decision.reason == "profit_lock"

    def test_does_not_lock_when_best_r_never_reached_the_arm_level(self):
        state = TicketState(checks=2, lastR=0.8, bestR=0.8)
        decision = decide_scalp_action(ScalpDecisionInput(state=state, r=0.2, **SAFE), CFG)
        assert decision.action == "hold"


class TestStateTracking:
    def test_carries_the_running_max_and_latest_r_forward(self):
        state = TicketState(checks=2, lastR=0.3, bestR=0.9)
        decision = decide_scalp_action(ScalpDecisionInput(state=state, r=0.4, **SAFE), CFG)
        assert decision.nextState == TicketState(checks=3, lastR=0.4, bestR=0.9)


class TestLoadConfig:
    def test_defaults_match_the_documented_thresholds(self):
        assert load_config() == CFG

    def test_env_overrides_are_honoured(self, monkeypatch):
        from app.core.settings import get_settings

        monkeypatch.setenv("SCALP_EMERGENCY_R", "1.25")
        get_settings.cache_clear()
        assert load_config().emergencyR == 1.25
