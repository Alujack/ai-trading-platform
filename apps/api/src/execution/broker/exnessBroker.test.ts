import { afterEach, describe, expect, it, vi } from "vitest";
import { ExnessBroker } from "./exnessBroker";
import { BrokerError } from "./types";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function broker() {
  return new ExnessBroker({ baseUrl: "http://bridge:8800/", token: "secret", env: "demo" });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ExnessBroker", () => {
  it("labels itself by environment", () => {
    expect(new ExnessBroker({ baseUrl: "x", token: "t", env: "demo" }).name).toBe("exness_demo");
    expect(new ExnessBroker({ baseUrl: "x", token: "t", env: "real" }).name).toBe("exness_real");
  });

  it("places an order with the bridge token, mapped symbol and SL/TP", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ status: "filled", ticket: 12345, fillPrice: 1.1002 }));

    const r = await broker().placeOrder({
      symbol: "EURUSD",
      side: "SHORT",
      lots: 0.2,
      stopLoss: 1.1050,
      takeProfit: 1.0950,
      clientTag: "sig-9",
    });

    expect(r.status).toBe("filled");
    expect(r.ticket).toBe(12345);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://bridge:8800/order"); // trailing slash trimmed
    expect(init?.method).toBe("POST");
    expect((init?.headers as Record<string, string>)["x-bridge-token"]).toBe("secret");
    const sent = JSON.parse(init?.body as string);
    expect(sent).toMatchObject({
      symbol: "EURUSD",
      side: "SHORT",
      lots: 0.2,
      sl: 1.1050,
      tp: 1.0950,
      clientTag: "sig-9",
    });
    expect(sent.deviation).toBe(20); // default slippage
  });

  it("maps symbol spec fields from the bridge", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        symbol: "EURUSD",
        digits: 5,
        point: 1e-5,
        contractSize: 100_000,
        volumeMin: 0.01,
        volumeStep: 0.01,
        volumeMax: 100,
        tickValue: 1,
      }),
    );
    const spec = await broker().getSymbolSpec("EURUSD");
    expect(spec.contractSize).toBe(100_000);
    expect(spec.volumeStep).toBe(0.01);
  });

  it("normalizes positions (ticket→string, side, clientTag)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        positions: [
          {
            ticket: 777,
            symbol: "EURUSD",
            side: "LONG",
            lots: 0.1,
            openPrice: 1.1,
            stopLoss: 1.09,
            takeProfit: 1.12,
            profit: 4.2,
            clientTag: "sig-3",
          },
        ],
      }),
    );
    const pos = await broker().getPositions();
    expect(pos[0].ticket).toBe("777");
    expect(pos[0].side).toBe("LONG");
    expect(pos[0].clientTag).toBe("sig-3");
  });

  it("throws BrokerError on a non-OK bridge response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ error: "no money" }, 500));
    await expect(broker().getAccount()).rejects.toBeInstanceOf(BrokerError);
  });

  it("health() returns ok:false instead of throwing when unreachable", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("ECONNREFUSED"));
    const h = await broker().health();
    expect(h.ok).toBe(false);
    expect(h.detail).toContain("ECONNREFUSED");
  });
});
