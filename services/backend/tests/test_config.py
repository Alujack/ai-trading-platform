"""Config layering, bounds and the flag resolution order.

The resolver is the seam every risk decision reads through, so the
most-specific-wins layering and the "disabled row acts as absent" rule are
tested directly against stub rows rather than a live database.
"""
from __future__ import annotations

import pytest

from app.core.settings import get_settings
from app.db.enums import ExecutionMode
from app.domain.config import resolve as resolve_mod
from app.domain.config.defaults import (
    RISK_BOUNDS,
    RISK_FIELDS,
    SYMBOL_CURRENCIES,
    bounds_wire,
    risk_defaults,
)
from app.domain.config.resolve import (
    ExecRow,
    get_execution_map,
    resolve_execution_mode,
    resolve_risk_config,
)
from app.domain.config.store import validate_risk_fields


def risk_row(scope: str, scope_key: str, enabled: bool = True, **fields) -> dict:
    row = {"scope": scope, "scopeKey": scope_key, "enabled": enabled}
    row.update({f: None for f in RISK_FIELDS})
    row.update(fields)
    return row


@pytest.fixture
def stub_rows(monkeypatch: pytest.MonkeyPatch):
    """Replace the cached DB reads with in-memory rows."""

    def _install(risk: list[dict], execution: list[ExecRow] | None = None):
        async def _risk(_session):
            return risk

        async def _exec(_session):
            return execution or []

        monkeypatch.setattr(resolve_mod, "_risk_rows", _risk)
        monkeypatch.setattr(resolve_mod, "_exec_rows", _exec)

    return _install


class TestResolveRiskConfig:
    async def test_falls_back_to_code_defaults_with_no_rows(self, stub_rows):
        stub_rows([])
        assert await resolve_risk_config(None) == risk_defaults()

    async def test_global_row_overrides_the_default(self, stub_rows):
        stub_rows([risk_row("GLOBAL", "", minRR=3.0)])
        assert (await resolve_risk_config(None)).minRR == 3.0

    async def test_symbol_beats_strategy_beats_global_per_field(self, stub_rows):
        stub_rows(
            [
                risk_row("GLOBAL", "", minRR=2.0, riskPerTradePct=1.0, aiMinScore=70),
                risk_row("STRATEGY", "sweep_mss", minRR=2.5, riskPerTradePct=0.5),
                risk_row("SYMBOL", "XAUUSD", minRR=3.0),
            ]
        )
        cfg = await resolve_risk_config(None, "sweep_mss", "XAUUSD")
        assert cfg.minRR == 3.0  # SYMBOL wins
        assert cfg.riskPerTradePct == 0.5  # STRATEGY wins (SYMBOL is null here)
        assert cfg.aiMinScore == 70  # GLOBAL wins (both narrower scopes null)

    async def test_a_disabled_row_is_ignored_entirely(self, stub_rows):
        stub_rows(
            [
                risk_row("GLOBAL", "", minRR=2.0),
                risk_row("SYMBOL", "XAUUSD", enabled=False, minRR=9.0),
            ]
        )
        assert (await resolve_risk_config(None, None, "XAUUSD")).minRR == 2.0

    async def test_a_non_matching_scope_key_does_not_apply(self, stub_rows):
        stub_rows([risk_row("SYMBOL", "EURUSD", minRR=9.0)])
        assert (await resolve_risk_config(None, None, "XAUUSD")).minRR == risk_defaults().minRR

    async def test_global_rows_are_matched_on_an_empty_scope_key(self, stub_rows):
        # A GLOBAL row is stored with scopeKey "", and must match even when the
        # caller passes a symbol.
        stub_rows([risk_row("GLOBAL", "", maxOpenTrades=4)])
        assert (await resolve_risk_config(None, "any", "XAUUSD")).maxOpenTrades == 4


class TestResolveExecutionMode:
    async def test_defaults_to_confirm_with_no_rows(self, stub_rows):
        stub_rows([], [])
        assert await resolve_execution_mode(None) == ExecutionMode.CONFIRM

    async def test_symbol_row_wins_over_strategy_and_global(self, stub_rows):
        stub_rows(
            [],
            [
                ExecRow("GLOBAL", "", "AUTO"),
                ExecRow("STRATEGY", "sweep_mss", "CONFIRM"),
                ExecRow("SYMBOL", "XAUUSD", "OFF"),
            ],
        )
        assert await resolve_execution_mode(None, "sweep_mss", "XAUUSD") == ExecutionMode.OFF

    async def test_strategy_row_wins_over_global(self, stub_rows):
        stub_rows([], [ExecRow("GLOBAL", "", "AUTO"), ExecRow("STRATEGY", "s", "OFF")])
        assert await resolve_execution_mode(None, "s", "XAUUSD") == ExecutionMode.OFF

    async def test_execution_map_reports_the_effective_global(self, stub_rows):
        stub_rows([], [ExecRow("GLOBAL", "", "OFF"), ExecRow("SYMBOL", "XAUUSD", "AUTO")])
        result = await get_execution_map(None)
        assert result["global"] == "OFF"
        assert len(result["rows"]) == 2

    async def test_execution_map_defaults_to_confirm_when_no_global_row_exists(self, stub_rows):
        stub_rows([], [ExecRow("SYMBOL", "XAUUSD", "AUTO")])
        assert (await get_execution_map(None))["global"] == "CONFIRM"


class TestValidateRiskFields:
    def test_accepts_values_inside_the_bounds(self):
        assert validate_risk_fields({"minRR": 2.0, "aiMinScore": 70}) is None

    def test_accepts_an_empty_patch(self):
        assert validate_risk_fields({}) is None

    def test_rejects_a_value_below_the_minimum(self):
        assert validate_risk_fields({"riskPerTradePct": 0.001}) == (
            "riskPerTradePct must be between 0.01 and 5"
        )

    def test_rejects_a_value_above_the_maximum(self):
        assert validate_risk_fields({"minRR": 99}) == "minRR must be between 1 and 10"

    def test_rejects_a_non_integer_for_an_integer_field(self):
        assert validate_risk_fields({"maxOpenTrades": 2.5}) == "maxOpenTrades must be an integer"

    def test_accepts_an_integral_float_for_an_integer_field(self):
        assert validate_risk_fields({"maxOpenTrades": 2.0}) is None

    def test_rejects_a_non_numeric_value(self):
        assert validate_risk_fields({"minRR": "three"}) == "minRR must be a number"

    def test_rejects_a_boolean_masquerading_as_a_number(self):
        # `True` is an int in Python; letting it through would set minRR = 1.
        assert validate_risk_fields({"minRR": True}) == "minRR must be a number"

    def test_rejects_nan_and_infinity(self):
        assert validate_risk_fields({"minRR": float("nan")}) is not None
        assert validate_risk_fields({"minRR": float("inf")}) is not None

    def test_every_effective_field_has_a_bound(self):
        # A new risk field without a bound would be settable to anything.
        assert set(RISK_BOUNDS) == set(RISK_FIELDS)


class TestBoundsWire:
    def test_renders_int_flags_only_where_they_apply(self):
        wire = bounds_wire()
        assert wire["maxOpenTrades"] == {"min": 1, "max": 100, "int": True}
        assert wire["minRR"] == {"min": 1, "max": 10}


class TestDefaults:
    def test_reads_the_paper_risk_percent_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("PAPER_RISK_PERCENT", "0.5")
        get_settings.cache_clear()
        assert risk_defaults().riskPerTradePct == 0.5

    def test_every_traded_symbol_has_a_currency_mapping(self):
        # A missing entry silently disables the per-currency exposure cap.
        assert set(SYMBOL_CURRENCIES) == {"XAUUSD", "EURUSD", "BTCUSD"}
        assert all("USD" in ccys for ccys in SYMBOL_CURRENCIES.values())


class TestFlagResolution:
    async def test_db_row_beats_the_env_default(self, monkeypatch):
        from app.domain.config import flags

        monkeypatch.setenv("RAW_SIGNAL_FEED", "true")
        get_settings.cache_clear()

        async def _rows(_session):
            return [{"key": flags.RAW_FEED_FLAG, "enabled": False}]

        monkeypatch.setattr(flags, "_flag_rows", _rows)
        state = await flags.get_flag(None, flags.RAW_FEED_FLAG)
        assert state.enabled is False
        assert state.source == "db"

    async def test_env_default_applies_with_no_db_row(self, monkeypatch):
        from app.domain.config import flags

        monkeypatch.setenv("RAW_SIGNAL_FEED", "true")
        get_settings.cache_clear()

        async def _rows(_session):
            return []

        monkeypatch.setattr(flags, "_flag_rows", _rows)
        state = await flags.get_flag(None, flags.RAW_FEED_FLAG)
        assert state.enabled is True
        assert state.source == "env"

    async def test_defaults_to_off_with_neither_row_nor_env(self, monkeypatch):
        from app.domain.config import flags

        monkeypatch.delenv("RAW_SIGNAL_FEED", raising=False)
        get_settings.cache_clear()

        async def _rows(_session):
            return []

        monkeypatch.setattr(flags, "_flag_rows", _rows)
        state = await flags.get_flag(None, flags.RAW_FEED_FLAG)
        assert state.enabled is False
        assert state.source == "default"

    async def test_a_resolution_failure_can_only_turn_a_feature_off(self, monkeypatch):
        from app.domain.config import flags

        monkeypatch.delenv("RAW_SIGNAL_FEED", raising=False)
        get_settings.cache_clear()

        async def _boom(_session):
            raise RuntimeError("db down")

        monkeypatch.setattr(flags, "_flag_rows", _boom)
        assert await flags.is_flag_enabled(None, flags.RAW_FEED_FLAG) is False


class TestMaxOpenTradesDualDefault:
    """`PAPER_MAX_OPEN_TRADES` has two different fallbacks in the original.

    `config/defaults.ts` falls back to 1 (the risk-engine concurrency cap — the
    "one trade at a time" sticky rule), while `positions.routes.ts` falls back to
    5 for the dashboard's `maxOpen` display. Collapsing them onto 5 would loosen
    the concurrency cap five-fold, so both are pinned here.
    """

    def test_the_risk_cap_defaults_to_one(self, monkeypatch):
        monkeypatch.delenv("PAPER_MAX_OPEN_TRADES", raising=False)
        get_settings.cache_clear()
        assert risk_defaults().maxOpenTrades == 1

    def test_the_display_value_defaults_to_five(self, monkeypatch):
        monkeypatch.delenv("PAPER_MAX_OPEN_TRADES", raising=False)
        get_settings.cache_clear()
        assert get_settings().paper_max_open_trades_display == 5

    def test_an_explicit_env_value_applies_to_both(self, monkeypatch):
        monkeypatch.setenv("PAPER_MAX_OPEN_TRADES", "3")
        get_settings.cache_clear()
        assert risk_defaults().maxOpenTrades == 3
        assert get_settings().paper_max_open_trades_display == 3

    def test_a_malformed_env_value_fails_fast_instead_of_disabling_the_cap(self, monkeypatch):
        """A deliberate improvement over the Express behaviour.

        `Number("not-a-number")` is `NaN`, and `openedToday >= NaN` is always
        false — so in Express a typo'd `PAPER_MAX_OPEN_TRADES` silently switched
        the concurrency cap OFF. Pydantic refuses to construct the settings
        instead, so the service fails loudly at boot rather than trading
        unbounded.
        """
        from pydantic import ValidationError

        monkeypatch.setenv("PAPER_MAX_OPEN_TRADES", "not-a-number")
        get_settings.cache_clear()
        with pytest.raises(ValidationError):
            get_settings()
