"""HTTP contract tests: status codes, error shape, and the SSE stream.

These run against the real ASGI app with the database dependency stubbed, so
they exercise routing, request validation and the error handlers — the parts the
dashboard depends on that don't need real rows. The lifespan is deliberately not
started: it would connect to Postgres/Redis and launch the schedulers, which is
not what these cases are about.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_session
from app.main import app


class _EmptyResult:
    """Mimics just enough of SQLAlchemy's Result for the read routes."""

    def scalars(self):
        return self

    def all(self):
        return []

    def scalar(self):
        return None

    def scalar_one_or_none(self):
        return None


class FakeSession:
    """A session whose queries all come back empty — the "empty state" fixture."""

    async def execute(self, _statement, *args, **kwargs):
        return _EmptyResult()

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def flush(self):
        return None

    def add(self, _obj):
        return None


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BACKEND_JOB_OWNER", "false")

    async def _fake_session():
        yield FakeSession()

    app.dependency_overrides[get_session] = _fake_session
    # No `with` block: that would run the lifespan (DB/Redis connect + jobs).
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestHealth:
    def test_liveness_touches_no_dependency(self, client: TestClient):
        res = client.get("/health/live")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    def test_root_health_matches_the_express_shape(self, client: TestClient):
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}


class TestErrorShape:
    def test_unknown_route_reports_the_express_message(self, client: TestClient):
        res = client.get("/api/does-not-exist")
        assert res.status_code == 404
        assert res.json() == {"error": "Route not found: GET /api/does-not-exist"}

    def test_validation_failure_uses_the_zod_flatten_shape(self, client: TestClient):
        # `timeframe` is required and must be one of the five labels.
        res = client.get("/api/candles", params={"symbol": "XAUUSD", "timeframe": "3min"})
        assert res.status_code == 400
        body = res.json()
        assert body["error"] == "Validation failed"
        assert "fieldErrors" in body["details"]
        assert "formErrors" in body["details"]
        assert "timeframe" in body["details"]["fieldErrors"]

    def test_out_of_range_limit_is_rejected(self, client: TestClient):
        res = client.get(
            "/api/candles", params={"symbol": "XAUUSD", "timeframe": "60min", "limit": 5000}
        )
        assert res.status_code == 400
        assert res.json()["error"] == "Validation failed"

    def test_every_error_body_carries_an_error_key(self, client: TestClient):
        # The web client reads `body.error` off any failure; nothing may return
        # FastAPI's default `{"detail": ...}`.
        for path, params in (
            ("/api/candles", {}),
            ("/api/signals", {"limit": 0}),
            ("/api/journal", {"limit": 999}),
        ):
            res = client.get(path, params=params)
            assert res.status_code == 400, path
            assert "error" in res.json(), path
            assert "detail" not in res.json(), path


class TestEmptyStates:
    def test_candles_returns_a_bare_array(self, client: TestClient):
        res = client.get("/api/candles", params={"symbol": "XAUUSD", "timeframe": "60min"})
        assert res.status_code == 200
        assert res.json() == []

    def test_symbols_returns_a_keyed_list(self, client: TestClient):
        res = client.get("/api/symbols")
        assert res.status_code == 200
        assert res.json() == {"symbols": []}

    def test_signals_returns_data_plus_pagination(self, client: TestClient):
        res = client.get("/api/signals", params={"limit": 10, "offset": 5})
        assert res.status_code == 200
        assert res.json() == {
            "data": [],
            "pagination": {"limit": 10, "offset": 5, "total": 0},
        }

    def test_journal_returns_data_plus_count(self, client: TestClient):
        res = client.get("/api/journal")
        assert res.status_code == 200
        assert res.json() == {"data": [], "count": 0}

    def test_news_returns_data_plus_count(self, client: TestClient):
        res = client.get("/api/news")
        assert res.status_code == 200
        assert res.json() == {"data": [], "count": 0}

    def test_performance_returns_the_zeroed_metric_set(self, client: TestClient):
        res = client.get("/api/performance")
        assert res.status_code == 200
        assert res.json() == {
            "totalTrades": 0,
            "winRate": 0,
            "totalPnL": 0,
            "maxDrawdown": 0,
            "averageRR": 0,
            "expectancy": 0,
            "profitFactor": 0,
        }

    def test_positions_returns_an_account_summary_and_no_positions(self, client: TestClient):
        res = client.get("/api/positions")
        assert res.status_code == 200
        body = res.json()
        assert body["positions"] == []
        assert body["account"]["openCount"] == 0
        assert body["account"]["baseBalance"] == 10000

    def test_missing_signal_returns_the_not_found_body(self, client: TestClient):
        res = client.get("/api/signals/nope")
        assert res.status_code == 404
        assert res.json() == {"error": "Not found"}

    def test_missing_backtest_run_returns_its_own_message(self, client: TestClient):
        res = client.get("/api/backtests/nope")
        assert res.status_code == 404
        assert res.json() == {"error": "Backtest run not found"}


class TestRouteOrdering:
    def test_signals_raw_wins_over_the_param_route(self, client: TestClient):
        # If `/api/signals/{id}` shadowed this, the body would be a 404 instead.
        res = client.get("/api/signals/raw")
        assert res.status_code == 200
        body = res.json()
        assert "feedEnabled" in body
        assert body["data"] == []

    def test_backtests_run_status_wins_over_the_param_route(self, client: TestClient):
        res = client.get("/api/backtests/run/status")
        assert res.status_code == 200
        assert "running" in res.json()


class TestCandidateValidation:
    def test_rejects_a_non_positive_entry_price(self, client: TestClient):
        res = client.post(
            "/api/signals/candidate",
            json={
                "strategyName": "s",
                "symbol": "XAUUSD",
                "timeframe": "60min",
                "direction": "LONG",
                "entryPrice": 0,
                "stopLoss": 1,
                "takeProfit": 2,
                "confidence": 50,
                "reasoning": "why",
            },
        )
        assert res.status_code == 400
        assert "entryPrice" in res.json()["details"]["fieldErrors"]

    def test_rejects_an_unknown_pre_gate_marker(self, client: TestClient):
        res = client.post(
            "/api/signals/candidate",
            json={
                "strategyName": "s",
                "symbol": "XAUUSD",
                "timeframe": "60min",
                "direction": "LONG",
                "entryPrice": 2400,
                "stopLoss": 2395,
                "takeProfit": 2410,
                "confidence": 50,
                "reasoning": "why",
                "preGatedBy": "made_up_layer",
            },
        )
        assert res.status_code == 400


class TestNewsAlert:
    def test_requires_title_and_scheduled_at(self, client: TestClient):
        res = client.post("/api/internal/news-alert", json={"currency": "USD"})
        assert res.status_code == 400
        assert res.json() == {"error": "title and scheduledAt are required"}


class TestTelegramWebhookAuth:
    def test_rejects_a_wrong_secret_token(self, client: TestClient, monkeypatch):
        from app.integrations.telegram import config as tg_config

        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cr3t")
        tg_config.reset_cache()
        res = client.post(
            "/api/internal/telegram/webhook",
            json={"message": {"text": "/kill"}},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert res.status_code == 401
        assert res.json() == {"error": "bad secret"}
        tg_config.reset_cache()

    def test_accepts_the_configured_secret_token(self, client: TestClient, monkeypatch):
        from app.integrations.telegram import config as tg_config

        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cr3t")
        tg_config.reset_cache()
        res = client.post(
            "/api/internal/telegram/webhook",
            json={},
            headers={"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"},
        )
        assert res.status_code == 200
        assert res.json() == {"ok": True}
        tg_config.reset_cache()


class TestSse:
    """SSE frame contract.

    Driven against the generator with a stub Request rather than through
    TestClient: the stream only ends when the client disconnects, and
    TestClient's synchronous transport never reports that, so a client-driven
    test would hang instead of asserting.
    """

    def test_route_declares_the_no_buffering_headers(self):
        from app.api.routes.realtime import SSE_HEADERS

        assert SSE_HEADERS["Content-Type"] == "text/event-stream"
        assert SSE_HEADERS["Cache-Control"] == "no-cache, no-transform"
        assert SSE_HEADERS["X-Accel-Buffering"] == "no"
        assert SSE_HEADERS["Connection"] == "keep-alive"

    async def test_sends_hello_first_then_stops_on_disconnect(self):
        from app.api.routes.realtime import _event_stream

        class StubRequest:
            """Connected for the first poll, disconnected afterwards."""

            def __init__(self) -> None:
                self.polls = 0

            async def is_disconnected(self) -> bool:
                self.polls += 1
                return self.polls > 1

        frames = [frame async for frame in _event_stream(StubRequest())]
        assert frames[0] == b'event: hello\ndata: {"ok":true}\n\n'

    async def test_reports_an_error_frame_when_the_subscribe_fails(self, monkeypatch):
        """A Redis outage must degrade realtime, not break the stream."""
        from app.api.routes import realtime

        class BrokenPubSub:
            async def subscribe(self, *_a, **_k):
                raise RuntimeError("redis down")

            async def unsubscribe(self, *_a, **_k):
                return None

            async def aclose(self):
                return None

        class BrokenClient:
            def pubsub(self):
                return BrokenPubSub()

        monkeypatch.setattr(realtime, "redis_client", lambda: BrokenClient())

        class StubRequest:
            def __init__(self) -> None:
                self.polls = 0

            async def is_disconnected(self) -> bool:
                self.polls += 1
                return self.polls > 1

        frames = [frame async for frame in realtime._event_stream(StubRequest())]
        assert frames[0].startswith(b"event: hello")
        assert b'event: error\ndata: {"ok":false}\n\n' in frames
