import { describe, expect, it } from "vitest";
import { PaperBroker } from "./paperBroker";
import type { PlaceOrderRequest } from "./types";

function order(over: Partial<PlaceOrderRequest> = {}): PlaceOrderRequest {
  return {
    symbol: "EURUSD",
    side: "LONG",
    lots: 10_000,
    stopLoss: 1.0980,
    takeProfit: 1.1060,
    clientTag: "sig-1",
    referencePrice: 1.1000,
    ...over,
  };
}

describe("PaperBroker", () => {
  it("fills at the reference price and reports the open position", async () => {
    const b = new PaperBroker({ balance: 10_000 });
    const r = await b.placeOrder(order());
    expect(r.status).toBe("filled");
    expect(r.fillPrice).toBe(1.1000);
    const pos = await b.getPositions();
    expect(pos).toHaveLength(1);
    expect(pos[0].clientTag).toBe("sig-1");
  });

  it("is idempotent on clientTag — a retry never opens a 2nd position", async () => {
    const b = new PaperBroker();
    const first = await b.placeOrder(order());
    const retry = await b.placeOrder(order());
    expect(retry.status).toBe("filled");
    expect(retry.ticket).toBe(first.ticket);
    expect(await b.getPositions()).toHaveLength(1);
  });

  it("rejects without a reference price (paper needs a fill price)", async () => {
    const b = new PaperBroker();
    const r = await b.placeOrder(order({ referencePrice: undefined }));
    expect(r.status).toBe("rejected");
    expect(r.reason).toBe("paper_no_reference_price");
  });

  it("computes LONG profit on close", async () => {
    const b = new PaperBroker();
    const open = await b.placeOrder(order({ lots: 10_000, referencePrice: 1.1000 }));
    const close = await b.closePosition(open.ticket!, { referenceExitPrice: 1.1050 });
    expect(close.status).toBe("closed");
    // (1.1050 - 1.1000) * 10_000 = 50
    expect(close.profit).toBeCloseTo(50, 6);
    expect(await b.getPositions()).toHaveLength(0);
  });

  it("computes SHORT profit on close (inverted)", async () => {
    const b = new PaperBroker();
    const open = await b.placeOrder(order({ side: "SHORT", lots: 10_000, referencePrice: 1.1000 }));
    const close = await b.closePosition(open.ticket!, { referenceExitPrice: 1.0950 });
    // SHORT: (1.0950 - 1.1000) * 10_000 * -1 = 50
    expect(close.profit).toBeCloseTo(50, 6);
  });

  it("returns not_found closing an unknown ticket", async () => {
    const b = new PaperBroker();
    const r = await b.closePosition("nope");
    expect(r.status).toBe("not_found");
  });

  it("reports account equity including open profit after a mark", async () => {
    const b = new PaperBroker({ balance: 10_000 });
    const acct = await b.getAccount();
    expect(acct.balance).toBe(10_000);
    expect(acct.equity).toBe(10_000); // no marks applied → equity == balance
  });
});
