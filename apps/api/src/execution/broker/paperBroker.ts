/**
 * PaperBroker (Plan 08, Phase 2) — an in-process simulation conforming to the
 * Broker interface, so the same broker-routed execution path works for paper and
 * live. It is dependency-free and deterministic: opens fill at `referencePrice`
 * and closes at `referenceExitPrice`, so it is fully unit-testable without a
 * price feed or DB.
 *
 * NOTE: this is NOT yet wired into the live decider — the existing DB-backed
 * paperTrading engine still runs unchanged. Phase 4 unifies them.
 */
import type {
  Broker,
  BrokerAccount,
  BrokerPosition,
  ClosePositionOptions,
  ClosePositionResult,
  PlaceOrderRequest,
  PlaceOrderResult,
  SymbolSpec,
} from "./types";
import { brokerSymbol } from "./symbols";

export interface PaperBrokerOptions {
  balance?: number;
  currency?: string;
}

/** Generic 1:1 spec — paper "lots" equal units, preserving today's unit-based math. */
function paperSpec(symbol: string): SymbolSpec {
  return {
    symbol: brokerSymbol(symbol),
    digits: 5,
    point: 1e-5,
    contractSize: 1,
    volumeMin: 0,
    volumeStep: 1e-8,
    volumeMax: Number.MAX_SAFE_INTEGER,
    tickValue: 1,
  };
}

export class PaperBroker implements Broker {
  readonly name = "paper";
  private readonly balance: number;
  private readonly currency: string;
  private readonly positions = new Map<string, BrokerPosition>();
  private readonly byTag = new Map<string, string>();
  private seq = 0;

  constructor(opts: PaperBrokerOptions = {}) {
    this.balance = opts.balance ?? Number(process.env.PAPER_ACCOUNT_BALANCE ?? "10000");
    this.currency = opts.currency ?? "USD";
  }

  async health(): Promise<{ ok: boolean; detail?: string }> {
    return { ok: true, detail: "paper simulator" };
  }

  async getAccount(): Promise<BrokerAccount> {
    const openProfit = [...this.positions.values()].reduce((s, p) => s + p.profit, 0);
    return { balance: this.balance, equity: this.balance + openProfit, currency: this.currency };
  }

  async getSymbolSpec(symbol: string): Promise<SymbolSpec> {
    return paperSpec(symbol);
  }

  async placeOrder(req: PlaceOrderRequest): Promise<PlaceOrderResult> {
    // Idempotency: a repeated clientTag returns the original fill, never a 2nd position.
    const existingTicket = this.byTag.get(req.clientTag);
    if (existingTicket) {
      const pos = this.positions.get(existingTicket);
      return { status: "filled", ticket: existingTicket, fillPrice: pos?.openPrice };
    }
    if (!Number.isFinite(req.referencePrice)) {
      return { status: "rejected", reason: "paper_no_reference_price" };
    }
    if (!(req.lots > 0)) {
      return { status: "rejected", reason: "non_positive_lots" };
    }
    const ticket = `paper-${++this.seq}`;
    const fillPrice = req.referencePrice as number;
    this.positions.set(ticket, {
      ticket,
      symbol: brokerSymbol(req.symbol),
      side: req.side,
      lots: req.lots,
      openPrice: fillPrice,
      stopLoss: req.stopLoss,
      takeProfit: req.takeProfit,
      profit: 0,
      clientTag: req.clientTag,
    });
    this.byTag.set(req.clientTag, ticket);
    return { status: "filled", ticket, fillPrice };
  }

  async closePosition(ticket: string, opts: ClosePositionOptions = {}): Promise<ClosePositionResult> {
    const pos = this.positions.get(ticket);
    if (!pos) return { status: "not_found", ticket, reason: "unknown_ticket" };
    const exitPrice = opts.referenceExitPrice ?? pos.openPrice;
    const sign = pos.side === "LONG" ? 1 : -1;
    // contractSize folded into lots==units (paperSpec), so profit = Δprice * lots * sign.
    const profit = (exitPrice - pos.openPrice) * pos.lots * sign;
    this.positions.delete(ticket);
    if (pos.clientTag) this.byTag.delete(pos.clientTag);
    return { status: "closed", ticket, exitPrice, profit };
  }

  async getPositions(): Promise<BrokerPosition[]> {
    return [...this.positions.values()];
  }
}
