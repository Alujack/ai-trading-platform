/**
 * Broker abstraction (Plan 08, Phase 2).
 *
 * A `Broker` is the seam between the execution decider and wherever an order
 * actually lives — a local simulation (`PaperBroker`) or a real account behind
 * the MT5 bridge (`ExnessBroker`). The risk engine + gate still run BEFORE any
 * broker call; a broker can only place/close/report, never loosen a risk check.
 *
 * Sizing is expressed in LOTS at this layer (already converted from the risk
 * engine's raw units via `lotsFromUnits`), because that is what MT5 trades in.
 */

export type OrderSide = "LONG" | "SHORT";

export interface BrokerAccount {
  balance: number;
  equity: number;
  currency: string;
  marginFree?: number;
  leverage?: number;
}

/** Contract/volume metadata for one symbol, needed to size and validate orders. */
export interface SymbolSpec {
  /** Broker-native symbol name (e.g. what MT5 Market Watch shows). */
  symbol: string;
  digits: number;
  point: number;
  /** Units of base instrument per 1.0 lot (FX major ≈ 100000; XAU ≈ 100). */
  contractSize: number;
  volumeMin: number;
  volumeStep: number;
  volumeMax: number;
  /** Account-currency value of one tick for 1.0 lot — the basis for $-risk sizing. */
  tickValue: number;
  bid?: number;
  ask?: number;
}

export interface PlaceOrderRequest {
  /** Internal symbol (EURUSD); the broker maps it to its native name. */
  symbol: string;
  side: OrderSide;
  /** Volume in lots, already step-clamped by the caller. */
  lots: number;
  stopLoss: number;
  takeProfit: number;
  /** Deterministic idempotency tag — our signalId. A retry must never double-fill. */
  clientTag: string;
  /** Max slippage in points (live only). */
  deviation?: number;
  /**
   * Paper-only: the price at which the simulator should fill. Live brokers fill
   * at market and IGNORE this field.
   */
  referencePrice?: number;
}

export interface PlaceOrderResult {
  status: "filled" | "rejected";
  ticket?: string;
  fillPrice?: number;
  reason?: string;
}

export interface ClosePositionOptions {
  /** Paper-only simulated exit price; live brokers close at market and ignore it. */
  referenceExitPrice?: number;
}

export interface ClosePositionResult {
  status: "closed" | "not_found" | "error";
  ticket?: string;
  exitPrice?: number;
  /** Realized profit in account currency. */
  profit?: number;
  reason?: string;
}

export interface BrokerPosition {
  ticket: string;
  /** Broker-native symbol as reported by the venue. */
  symbol: string;
  side: OrderSide;
  lots: number;
  openPrice: number;
  stopLoss: number;
  takeProfit: number;
  /** Unrealized profit in account currency. */
  profit: number;
  /** Our signalId, echoed back by the bridge for reconciliation. */
  clientTag?: string;
}

export interface Broker {
  /** "paper" | "exness_demo" | "exness_real" — used for labelling + the Trade.broker column. */
  readonly name: string;
  health(): Promise<{ ok: boolean; detail?: string }>;
  getAccount(): Promise<BrokerAccount>;
  getSymbolSpec(symbol: string): Promise<SymbolSpec>;
  placeOrder(req: PlaceOrderRequest): Promise<PlaceOrderResult>;
  closePosition(ticket: string, opts?: ClosePositionOptions): Promise<ClosePositionResult>;
  getPositions(): Promise<BrokerPosition[]>;
}

/** Raised when the broker/bridge is unreachable or returns a non-OK status. */
export class BrokerError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "BrokerError";
  }
}
