"""The layer switches must fail safe in the runner.

`_layer_config` is the worker's only view of which discretionary filters the
operator turned off. A wrong answer here silently changes what the desk trades,
so the two properties worth pinning are: a probe failure keeps every layer, and
a disabled layer's param override actually reaches the strategy.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from strategies import build_strategy
from strategy_runner import LayerConfig, _layer_config

ALL_ON = {
    "mode": "FULL",
    "layers": [
        {"key": "ai_validation", "enabled": True, "appliedBy": "gate", "param": None},
        {"key": "regime_gating", "enabled": True, "appliedBy": "strategy", "param": None},
        {"key": "killzone_gating", "enabled": True, "appliedBy": "strategy", "param": "useKillzone"},
        {"key": "bias_filter", "enabled": True, "appliedBy": "strategy", "param": "useBias"},
        {"key": "discount_filter", "enabled": True, "appliedBy": "strategy", "param": "requireDiscount"},
    ],
}


def _all_off() -> dict:
    return {
        "mode": "STRATEGY_ONLY",
        "layers": [{**layer, "enabled": False} for layer in ALL_ON["layers"]],
    }


def probe(handler) -> LayerConfig:
    """Run the async probe against a mocked API. The data service has no async
    pytest plugin, so the event loop is driven explicitly rather than adding one."""

    async def _go() -> LayerConfig:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _layer_config(client)

    return asyncio.run(_go())


def test_all_layers_on_yields_no_overrides():
    cfg = probe(lambda _r: httpx.Response(200, json=ALL_ON))
    assert cfg.all_on
    assert cfg.regime_gating is True
    assert cfg.param_overrides == {}


def test_strategy_only_turns_off_regime_and_every_param():
    cfg = probe(lambda _r: httpx.Response(200, json=_all_off()))
    assert cfg.regime_gating is False
    assert cfg.param_overrides == {
        "useKillzone": False,
        "useBias": False,
        "requireDiscount": False,
    }


def test_one_layer_off_leaves_the_others_alone():
    payload = {
        "mode": "MIXED",
        "layers": [
            {**layer, "enabled": layer["key"] != "killzone_gating"}
            for layer in ALL_ON["layers"]
        ],
    }
    cfg = probe(lambda _r: httpx.Response(200, json=payload))
    assert cfg.regime_gating is True
    assert cfg.param_overrides == {"useKillzone": False}


@pytest.mark.parametrize(
    "handler",
    [
        lambda _r: httpx.Response(500, text="boom"),
        lambda _r: httpx.Response(200, text="not json"),
        lambda _r: (_ for _ in ()).throw(httpx.ConnectError("refused")),
    ],
    ids=["http_500", "bad_body", "connect_error"],
)
def test_a_probe_failure_keeps_every_layer(handler):
    """Fail safe: an outage may only make the runner more selective, never less."""
    cfg = probe(handler)
    assert cfg == LayerConfig()
    assert cfg.all_on


def test_the_overrides_actually_disable_the_strategy_filters():
    """The param names must match the real constructor, or the switch is a no-op."""
    cfg = LayerConfig(
        regime_gating=False,
        param_overrides={"useKillzone": False, "useBias": False, "requireDiscount": False},
    )
    strategy = build_strategy("ict_confluence", {**cfg.param_overrides})
    assert strategy.use_killzone is False
    assert strategy.use_bias is False
    assert strategy.require_discount is False


def test_the_filters_are_on_by_default():
    strategy = build_strategy("ict_confluence", {})
    assert strategy.use_killzone is True
    assert strategy.use_bias is True
    assert strategy.require_discount is True
