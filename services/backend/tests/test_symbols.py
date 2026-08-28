"""Symbol-mapping and lot-conversion parity, translated from `symbols.test.ts`."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.core.settings import get_settings
from app.domain.execution.broker.symbols import (
    broker_symbol,
    lots_from_units,
    reset_symbol_map,
)
from app.domain.execution.broker.types import SymbolSpec

FX = SymbolSpec(
    symbol="EURUSD",
    digits=5,
    point=1e-5,
    contractSize=100_000,
    volumeMin=0.01,
    volumeStep=0.01,
    volumeMax=100,
    tickValue=1,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_symbol_map()
    yield
    reset_symbol_map()


def _set_map(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("BROKER_SYMBOL_MAP", value)
    get_settings.cache_clear()
    reset_symbol_map()


class TestBrokerSymbol:
    def test_defaults_to_identity_for_known_majors(self):
        assert broker_symbol("EURUSD") == "EURUSD"
        assert broker_symbol("XAUUSD") == "XAUUSD"

    def test_returns_identity_for_an_unmapped_symbol(self):
        assert broker_symbol("GBPUSD") == "GBPUSD"

    def test_honours_a_broker_symbol_map_override(self, monkeypatch):
        _set_map(monkeypatch, json.dumps({"EURUSD": "EURUSDm", "BTCUSD": "BTCUSDm"}))
        assert broker_symbol("EURUSD") == "EURUSDm"
        assert broker_symbol("BTCUSD") == "BTCUSDm"
        assert broker_symbol("XAUUSD") == "XAUUSD"  # unspecified falls back to default

    def test_falls_back_to_defaults_on_invalid_override_json(self, monkeypatch):
        _set_map(monkeypatch, "{not json")
        assert broker_symbol("EURUSD") == "EURUSD"


class TestLotsFromUnits:
    def test_converts_units_to_lots_via_contract_size(self):
        # 20,000 units / 100,000 = 0.20 lots
        assert lots_from_units(20_000, FX) == pytest.approx(0.2, abs=1e-8)

    def test_floors_to_the_volume_step(self):
        # 0.207 lots → floored to 0.20 at step 0.01
        assert lots_from_units(20_700, FX) == pytest.approx(0.2, abs=1e-8)

    def test_returns_zero_below_the_broker_minimum(self):
        # 500 units = 0.005 lots < volumeMin 0.01 — the caller must reject.
        assert lots_from_units(500, FX) == 0

    def test_clamps_to_the_broker_maximum(self):
        assert lots_from_units(1e12, FX) == FX.volumeMax

    def test_handles_a_different_contract_size(self):
        xau = replace(FX, symbol="XAUUSD", contractSize=100)
        # 250 units / 100 = 2.5 lots
        assert lots_from_units(250, xau) == pytest.approx(2.5, abs=1e-8)

    def test_returns_zero_for_non_positive_units(self):
        assert lots_from_units(0, FX) == 0
        assert lots_from_units(-5, FX) == 0

    def test_rejects_a_spec_with_no_contract_size(self):
        with pytest.raises(ValueError):
            lots_from_units(1_000, replace(FX, contractSize=0))
