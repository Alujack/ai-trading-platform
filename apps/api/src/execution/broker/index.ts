/**
 * Broker factory (Plan 08, Phase 2). Selects the execution broker from env:
 *   BROKER=paper            (default) → PaperBroker
 *   BROKER=exness           → ExnessBroker (requires MT5_BRIDGE_URL + MT5_BRIDGE_TOKEN)
 *   EXNESS_ENV=demo|real    → labels the live broker (demo default; real is the
 *                             promotion-gated flip, see Plan 08 §9)
 *
 * Going from demo to real is ONLY this env change — no code path differs.
 */
import { ExnessBroker } from "./exnessBroker";
import { PaperBroker } from "./paperBroker";
import type { Broker } from "./types";

let singleton: Broker | null = null;

export function getBroker(): Broker {
  if (singleton) return singleton;

  const kind = (process.env.BROKER ?? "paper").trim().toLowerCase();

  if (kind === "paper") {
    singleton = new PaperBroker();
    return singleton;
  }

  if (kind === "exness") {
    const baseUrl = process.env.MT5_BRIDGE_URL;
    const token = process.env.MT5_BRIDGE_TOKEN;
    if (!baseUrl) throw new Error("BROKER=exness requires MT5_BRIDGE_URL");
    if (!token) throw new Error("BROKER=exness requires MT5_BRIDGE_TOKEN");
    const env = (process.env.EXNESS_ENV ?? "demo").trim().toLowerCase() === "real" ? "real" : "demo";
    singleton = new ExnessBroker({ baseUrl, token, env });
    return singleton;
  }

  throw new Error(`unknown BROKER='${kind}' (expected paper|exness)`);
}

/** Test-only: drop the memoized broker so env changes take effect. */
export function __resetBroker(): void {
  singleton = null;
}

export * from "./types";
export { PaperBroker } from "./paperBroker";
export { ExnessBroker } from "./exnessBroker";
export { brokerSymbol, lotsFromUnits } from "./symbols";
