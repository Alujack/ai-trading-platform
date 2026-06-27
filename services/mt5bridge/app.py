"""MT5 bridge (Plan 08, Phase 1) — Windows-only.

A thin FastAPI service that runs beside a MetaTrader 5 terminal logged into an
Exness account and exposes the HTTP contract the main API's `ExnessBroker`
(apps/api/src/execution/broker/exnessBroker.ts) expects:

    GET  /health
    GET  /account
    GET  /symbol/{symbol}
    POST /order      {symbol, side, lots, sl, tp, clientTag, deviation}
    POST /close      {ticket}
    GET  /positions

Auth: every request must send  X-Bridge-Token: <MT5_BRIDGE_TOKEN>.

The MetaTrader5 library is NOT thread-safe, so every terminal call is serialized
behind a single lock. Run with ONE uvicorn worker.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from contextlib import asynccontextmanager

import MetaTrader5 as mt5  # Windows-only package
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s mt5bridge %(message)s")
log = logging.getLogger("mt5bridge")

MT5_LOGIN = int(os.environ["MT5_LOGIN"]) if os.environ.get("MT5_LOGIN") else None
MT5_PASSWORD = os.environ.get("MT5_PASSWORD")
MT5_SERVER = os.environ.get("MT5_SERVER")
MT5_TERMINAL_PATH = os.environ.get("MT5_TERMINAL_PATH") or None
BRIDGE_TOKEN = os.environ.get("MT5_BRIDGE_TOKEN", "")
DEFAULT_DEVIATION = int(os.environ.get("DEFAULT_DEVIATION", "20"))

_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #
def _ensure_connected() -> None:
    """Initialize + log in the terminal if not already connected. Caller holds _lock."""
    if mt5.terminal_info() is not None and mt5.account_info() is not None:
        return
    kwargs = {}
    if MT5_TERMINAL_PATH:
        kwargs["path"] = MT5_TERMINAL_PATH
    if MT5_LOGIN and MT5_PASSWORD and MT5_SERVER:
        kwargs.update(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    if not mt5.initialize(**kwargs):
        code, msg = mt5.last_error()
        raise HTTPException(503, f"mt5 initialize failed: ({code}) {msg}")
    if mt5.account_info() is None:
        # initialize() may attach without logging in — force a login.
        if not (MT5_LOGIN and mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)):
            code, msg = mt5.last_error()
            raise HTTPException(503, f"mt5 login failed: ({code}) {msg}")


def _select_symbol(sym: str):
    info = mt5.symbol_info(sym)
    if info is None:
        raise HTTPException(404, f"unknown symbol '{sym}' — check Market Watch name")
    if not info.visible and not mt5.symbol_select(sym, True):
        raise HTTPException(404, f"could not select symbol '{sym}'")
    return mt5.symbol_info(sym)


def _magic(client_tag: str) -> int:
    return int(hashlib.sha1(client_tag.encode()).hexdigest(), 16) % 2_000_000_000


def _find_position_by_tag(client_tag: str):
    magic = _magic(client_tag)
    for p in mt5.positions_get() or []:
        if p.comment == client_tag or p.magic == magic:
            return p
    return None


# --------------------------------------------------------------------------- #
# Auth + app
# --------------------------------------------------------------------------- #
def require_token(x_bridge_token: str = Header(default="")) -> None:
    if not BRIDGE_TOKEN or x_bridge_token != BRIDGE_TOKEN:
        raise HTTPException(401, "bad or missing X-Bridge-Token")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    with _lock:
        try:
            _ensure_connected()
            acct = mt5.account_info()
            log.info("connected login=%s server=%s balance=%s",
                     getattr(acct, "login", "?"), MT5_SERVER, getattr(acct, "balance", "?"))
        except Exception as exc:  # noqa: BLE001 — log, let /health report it
            log.error("startup connect failed: %s", exc)
    yield
    with _lock:
        mt5.shutdown()


app = FastAPI(title="MT5 Bridge", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class OrderReq(BaseModel):
    symbol: str
    side: str  # "LONG" | "SHORT"
    lots: float
    sl: float
    tp: float
    clientTag: str
    deviation: int | None = None


class CloseReq(BaseModel):
    ticket: int


class SessionLoginReq(BaseModel):
    login: int
    password: str
    server: str


_TF_MAP: dict[str, int] = {
    "1min": mt5.TIMEFRAME_M1,
    "M1":   mt5.TIMEFRAME_M1,
    "5min": mt5.TIMEFRAME_M5,
    "M5":   mt5.TIMEFRAME_M5,
    "15min": mt5.TIMEFRAME_M15,
    "M15":  mt5.TIMEFRAME_M15,
    "60min": mt5.TIMEFRAME_H1,
    "H1":   mt5.TIMEFRAME_H1,
    "daily": mt5.TIMEFRAME_D1,
    "D1":   mt5.TIMEFRAME_D1,
}


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@app.get("/health")
def health(_: None = Depends(require_token)):
    with _lock:
        try:
            _ensure_connected()
            acct = mt5.account_info()
            return {"ok": acct is not None,
                    "detail": f"login={acct.login} server={MT5_SERVER}" if acct else "no account"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": str(exc)}


@app.post("/session/login")
def session_login(req: SessionLoginReq, _: None = Depends(require_token)):
    """Log the terminal into a specific account at runtime — creds come from the
    main API (set in the web UI), not this bridge's .env. Replaces any current
    session and updates the in-process creds so later reconnects use this account.
    Returns {ok, detail}; a bad login is a clean ok=False, not a 5xx, so the
    settings UI gets a usable verdict.
    """
    global MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
    with _lock:
        try:
            mt5.shutdown()
        except Exception:  # noqa: BLE001 — best effort; terminal may not be initialized yet
            pass
        kwargs = {"login": req.login, "password": req.password, "server": req.server}
        if MT5_TERMINAL_PATH:
            kwargs["path"] = MT5_TERMINAL_PATH
        if not mt5.initialize(**kwargs):
            code, msg = mt5.last_error()
            return {"ok": False, "detail": f"initialize failed: ({code}) {msg}"}
        if mt5.account_info() is None and not mt5.login(req.login, password=req.password, server=req.server):
            code, msg = mt5.last_error()
            return {"ok": False, "detail": f"login failed: ({code}) {msg}"}
        acct = mt5.account_info()
        if acct is None:
            return {"ok": False, "detail": "no account info after login"}
        # Persist for reconnects within this process lifetime.
        MT5_LOGIN, MT5_PASSWORD, MT5_SERVER = req.login, req.password, req.server
        return {"ok": True, "detail": f"login={acct.login} server={req.server} balance={acct.balance}"}


@app.get("/account")
def account(_: None = Depends(require_token)):
    with _lock:
        _ensure_connected()
        a = mt5.account_info()
        if a is None:
            raise HTTPException(503, "no account info")
        return {"balance": a.balance, "equity": a.equity, "currency": a.currency,
                "marginFree": a.margin_free, "leverage": a.leverage}


@app.get("/symbol/{symbol}")
def symbol_spec(symbol: str, _: None = Depends(require_token)):
    with _lock:
        _ensure_connected()
        info = _select_symbol(symbol)
        tick = mt5.symbol_info_tick(symbol)
        return {
            "symbol": info.name,
            "digits": info.digits,
            "point": info.point,
            "contractSize": info.trade_contract_size,
            "volumeMin": info.volume_min,
            "volumeStep": info.volume_step,
            "volumeMax": info.volume_max,
            "tickValue": info.trade_tick_value,
            "bid": getattr(tick, "bid", None),
            "ask": getattr(tick, "ask", None),
        }


@app.post("/order")
def place_order(req: OrderReq, _: None = Depends(require_token)):
    with _lock:
        _ensure_connected()
        # Idempotency: a repeated clientTag must never open a second position.
        existing = _find_position_by_tag(req.clientTag)
        if existing is not None:
            return {"status": "filled", "ticket": existing.ticket,
                    "fillPrice": existing.price_open, "reason": "idempotent_existing"}

        info = _select_symbol(req.symbol)
        tick = mt5.symbol_info_tick(req.symbol)
        if tick is None:
            raise HTTPException(503, "no tick for symbol")
        is_long = req.side.upper() == "LONG"
        order_type = mt5.ORDER_TYPE_BUY if is_long else mt5.ORDER_TYPE_SELL
        price = tick.ask if is_long else tick.bid
        deviation = req.deviation if req.deviation is not None else DEFAULT_DEVIATION

        base = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": info.name,
            "volume": float(req.lots),
            "type": order_type,
            "price": price,
            "sl": float(req.sl),
            "tp": float(req.tp),
            "deviation": deviation,
            "magic": _magic(req.clientTag),
            "comment": req.clientTag[:31],
            "type_time": mt5.ORDER_TIME_GTC,
        }
        # Brokers differ on supported fill policy — try IOC, then FOK, then RETURN.
        last = None
        for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN):
            result = mt5.order_send({**base, "type_filling": filling})
            if result is None:
                code, msg = mt5.last_error()
                last = f"order_send None: ({code}) {msg}"
                continue
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                return {"status": "filled", "ticket": result.order, "fillPrice": result.price}
            last = f"retcode={result.retcode} {result.comment}"
            # Only the unsupported-filling code is worth retrying with another mode.
            if result.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
                break
        return {"status": "rejected", "reason": last or "unknown"}


@app.post("/close")
def close_position(req: CloseReq, _: None = Depends(require_token)):
    with _lock:
        _ensure_connected()
        positions = mt5.positions_get(ticket=req.ticket)
        if not positions:
            return {"status": "not_found", "ticket": req.ticket, "reason": "unknown_ticket"}
        p = positions[0]
        tick = mt5.symbol_info_tick(p.symbol)
        is_long = p.type == mt5.POSITION_TYPE_BUY
        close_type = mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_long else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": p.volume,
            "type": close_type,
            "position": p.ticket,
            "price": price,
            "deviation": DEFAULT_DEVIATION,
            "magic": p.magic,
            "comment": "close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            reason = (f"retcode={result.retcode} {result.comment}" if result
                      else f"order_send None: {mt5.last_error()}")
            return {"status": "error", "ticket": req.ticket, "reason": reason}
        # Realized profit: sum deals for this position id from history.
        profit = _realized_profit(p.ticket)
        return {"status": "closed", "ticket": req.ticket,
                "exitPrice": result.price, "profit": profit}


@app.get("/positions")
def positions(_: None = Depends(require_token)):
    with _lock:
        _ensure_connected()
        out = []
        for p in mt5.positions_get() or []:
            out.append({
                "ticket": p.ticket,
                "symbol": p.symbol,
                "side": "LONG" if p.type == mt5.POSITION_TYPE_BUY else "SHORT",
                "lots": p.volume,
                "openPrice": p.price_open,
                "stopLoss": p.sl,
                "takeProfit": p.tp,
                "profit": p.profit,
                "clientTag": p.comment or None,
            })
        return {"positions": out}


@app.get("/history/{ticket}")
def position_history(ticket: int, _: None = Depends(require_token)):
    """Return deal history for a closed position — used by the API's reconciliation loop.

    When a live position is closed by SL/TP (or manually in the terminal), the API
    detects that the ticket is gone from /positions and calls this endpoint to fetch
    the actual exit price and realized profit so it can write the correct Trade close.
    """
    with _lock:
        _ensure_connected()
        import time as _time
        deals = mt5.history_deals_get(position=ticket)
        if not deals:
            _time.sleep(0.3)
            deals = mt5.history_deals_get(position=ticket)
        if not deals:
            return {"found": False}
        # Sum all deal profits (covers commission, swap, and partial closes).
        total_profit = float(sum(d.profit for d in deals))
        # DEAL_ENTRY_OUT = 1; find the last exit deal to get the close price.
        exits = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
        ref = exits[-1] if exits else deals[-1]
        return {
            "found": True,
            "exitPrice": float(ref.price),
            "profit": total_profit,
            "closeTime": int(ref.time),
        }


def _realized_profit(position_id: int) -> float:
    """Sum the profit of all deals belonging to a (now-closed) position id."""
    import time
    deals = mt5.history_deals_get(position=position_id)
    if not deals:
        # history may lag a beat; one short retry.
        time.sleep(0.3)
        deals = mt5.history_deals_get(position=position_id)
    return float(sum(d.profit for d in deals)) if deals else 0.0


@app.get("/candles/{symbol}")
def candles(
    symbol: str,
    timeframe: str = "5min",
    count: int = 100,
    _: None = Depends(require_token),
):
    """Return the most recent `count` OHLCV bars for `symbol` from MT5.

    Timeframe accepts platform names (1min, 5min, 15min, 60min, daily)
    or MT5 names (M1, M5, M15, H1, D1).
    """
    tf = _TF_MAP.get(timeframe)
    if tf is None:
        raise HTTPException(400, f"unknown timeframe '{timeframe}'. Use: {list(_TF_MAP)}")
    if not (1 <= count <= 5000):
        raise HTTPException(400, "count must be between 1 and 5000")

    with _lock:
        _ensure_connected()
        _select_symbol(symbol)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)

    if rates is None or len(rates) == 0:
        raise HTTPException(404, f"no bar data for {symbol}/{timeframe} — is Market Watch open?")

    return [
        {
            "timestamp": int(r["time"]),
            "open":   float(r["open"]),
            "high":   float(r["high"]),
            "low":    float(r["low"]),
            "close":  float(r["close"]),
            "volume": int(r["tick_volume"]),
        }
        for r in rates
    ]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("BRIDGE_HOST", "0.0.0.0"),
                port=int(os.environ.get("BRIDGE_PORT", "8800")))
