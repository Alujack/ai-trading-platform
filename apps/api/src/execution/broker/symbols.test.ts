import { afterEach, describe, expect, it } from "vitest";
import { __resetSymbolMap, brokerSymbol, lotsFromUnits } from "./symbols";
import type { SymbolSpec } from "./types";

const FX: SymbolSpec = {
  symbol: "EURUSD",
  digits: 5,
  point: 1e-5,
  contractSize: 100_000,
  volumeMin: 0.01,
  volumeStep: 0.01,
  volumeMax: 100,
  tickValue: 1,
};

afterEach(() => {
  delete process.env.BROKER_SYMBOL_MAP;
  __resetSymbolMap();
});

describe("brokerSymbol", () => {
  it("defaults to identity for known majors", () => {
    expect(brokerSymbol("EURUSD")).toBe("EURUSD");
    expect(brokerSymbol("XAUUSD")).toBe("XAUUSD");
  });

  it("returns identity for an unmapped symbol", () => {
    expect(brokerSymbol("GBPUSD")).toBe("GBPUSD");
  });

  it("honors a BROKER_SYMBOL_MAP override (e.g. suffixed account)", () => {
    process.env.BROKER_SYMBOL_MAP = JSON.stringify({ EURUSD: "EURUSDm", BTCUSD: "BTCUSDm" });
    __resetSymbolMap();
    expect(brokerSymbol("EURUSD")).toBe("EURUSDm");
    expect(brokerSymbol("BTCUSD")).toBe("BTCUSDm");
    expect(brokerSymbol("XAUUSD")).toBe("XAUUSD"); // unspecified falls back to default
  });

  it("falls back to defaults on invalid override JSON", () => {
    process.env.BROKER_SYMBOL_MAP = "{not json";
    __resetSymbolMap();
    expect(brokerSymbol("EURUSD")).toBe("EURUSD");
  });
});

describe("lotsFromUnits", () => {
  it("converts units to lots via contract size", () => {
    // 20,000 units / 100,000 = 0.20 lots
    expect(lotsFromUnits(20_000, FX)).toBeCloseTo(0.2, 8);
  });

  it("floors to the volume step", () => {
    // 0.207 lots -> floored to 0.20 at step 0.01
    expect(lotsFromUnits(20_700, FX)).toBeCloseTo(0.2, 8);
  });

  it("returns 0 below the broker minimum (caller must reject)", () => {
    // 500 units = 0.005 lots < volumeMin 0.01
    expect(lotsFromUnits(500, FX)).toBe(0);
  });

  it("clamps to the broker maximum", () => {
    expect(lotsFromUnits(1e12, FX)).toBe(FX.volumeMax);
  });

  it("handles a different contract size (XAU ~ 100 oz/lot)", () => {
    const xau: SymbolSpec = { ...FX, symbol: "XAUUSD", contractSize: 100 };
    // 250 units / 100 = 2.5 lots
    expect(lotsFromUnits(250, xau)).toBeCloseTo(2.5, 8);
  });

  it("returns 0 for non-positive units", () => {
    expect(lotsFromUnits(0, FX)).toBe(0);
    expect(lotsFromUnits(-5, FX)).toBe(0);
  });
});
