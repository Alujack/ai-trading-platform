/**
 * ExnessBroker (Plan 08, Phase 2) — HTTP client for the MT5 bridge that runs on
 * the Windows host beside the MetaTrader 5 terminal. It speaks the bridge's JSON
 * contract (see services/mt5bridge, Phase 1); it holds no MT5 logic itself.
 *
 * The bridge is the only supported route to a retail Exness account (Exness has
 * no REST trading API). SL/TP are sent with every order so the broker manages
 * exits even if this service is down.
 */
import {
  BrokerError,
  type Broker,
  type BrokerAccount,
  type BrokerPosition,
  type ClosePositionOptions,
  type ClosePositionResult,
  type OrderSide,
  type PlaceOrderRequest,
  type PlaceOrderResult,
  type PositionHistory,
  type SymbolSpec,
} from "./types";
import { brokerSymbol } from "./symbols";

export interface ExnessBrokerConfig {
  baseUrl: string;
  token: string;
  env: "demo" | "real";
  timeoutMs?: number;
}

export class ExnessBroker implements Broker {
  readonly name: string;
  private readonly baseUrl: string;
  private readonly token: string;
  private readonly timeoutMs: number;

  constructor(cfg: ExnessBrokerConfig) {
    this.baseUrl = cfg.baseUrl.replace(/\/+$/, "");
    this.token = cfg.token;
    this.timeoutMs = cfg.timeoutMs ?? 10_000;
    this.name = `exness_${cfg.env}`;
  }

  private async call<T>(path: string, method: "GET" | "POST", body?: unknown): Promise<T> {
    let res: Response;
    try {
      res = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: {
          "content-type": "application/json",
          "x-bridge-token": this.token,
        },
        body: body == null ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      throw new BrokerError(`mt5 bridge unreachable: ${msg}`);
    }
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new BrokerError(`mt5 bridge ${path} -> http ${res.status}: ${text.slice(0, 200)}`, res.status);
    }
    return (await res.json()) as T;
  }

  async health(): Promise<{ ok: boolean; detail?: string }> {
    try {
      const r = await this.call<{ ok: boolean; detail?: string }>("/health", "GET");
      return { ok: !!r.ok, detail: r.detail };
    } catch (err) {
      return { ok: false, detail: err instanceof Error ? err.message : String(err) };
    }
  }

  async getAccount(): Promise<BrokerAccount> {
    return this.call<BrokerAccount>("/account", "GET");
  }

  async getSymbolSpec(symbol: string): Promise<SymbolSpec> {
    const native = brokerSymbol(symbol);
    return this.call<SymbolSpec>(`/symbol/${encodeURIComponent(native)}`, "GET");
  }

  async placeOrder(req: PlaceOrderRequest): Promise<PlaceOrderResult> {
    return this.call<PlaceOrderResult>("/order", "POST", {
      symbol: brokerSymbol(req.symbol),
      side: req.side,
      lots: req.lots,
      sl: req.stopLoss,
      tp: req.takeProfit,
      clientTag: req.clientTag,
      deviation: req.deviation ?? 20,
    });
  }

  // The live broker closes at market; referenceExitPrice (paper-only) is ignored.
  async closePosition(ticket: string, _opts?: ClosePositionOptions): Promise<ClosePositionResult> {
    return this.call<ClosePositionResult>("/close", "POST", { ticket });
  }

  async getPositions(): Promise<BrokerPosition[]> {
    const r = await this.call<{ positions: RawPosition[] }>("/positions", "GET");
    return (r.positions ?? []).map((p) => ({
      ticket: String(p.ticket),
      symbol: p.symbol,
      side: p.side as OrderSide,
      lots: p.lots,
      openPrice: p.openPrice,
      stopLoss: p.stopLoss,
      takeProfit: p.takeProfit,
      profit: p.profit,
      clientTag: p.clientTag,
    }));
  }

  async getPositionHistory(ticket: string): Promise<PositionHistory | null> {
    try {
      return await this.call<PositionHistory>(`/history/${encodeURIComponent(ticket)}`, "GET");
    } catch {
      return null;
    }
  }
}

interface RawPosition {
  ticket: string | number;
  symbol: string;
  side: string;
  lots: number;
  openPrice: number;
  stopLoss: number;
  takeProfit: number;
  profit: number;
  clientTag?: string;
}
