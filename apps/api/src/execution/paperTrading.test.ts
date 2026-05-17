import { describe, expect, it } from "vitest";
import { evaluateExit } from "./paperTrading";

describe("evaluateExit — LONG", () => {
  it("returns null when price is between SL and TP", () => {
    expect(evaluateExit("LONG", 100, 110, 90)).toBeNull();
  });

  it("exits at TP when price reaches the take-profit level", () => {
    expect(evaluateExit("LONG", 110, 110, 90)).toEqual({ exitPrice: 110, outcome: "win" });
  });

  it("exits at TP even when price overshoots take-profit", () => {
    // Clean-fill model: the limit-style TP order fills at the level, not the gap.
    expect(evaluateExit("LONG", 115, 110, 90)).toEqual({ exitPrice: 110, outcome: "win" });
  });

  it("exits at SL when price reaches the stop-loss level", () => {
    expect(evaluateExit("LONG", 90, 110, 90)).toEqual({ exitPrice: 90, outcome: "loss" });
  });

  it("exits at SL even when price gaps below stop-loss", () => {
    expect(evaluateExit("LONG", 85, 110, 90)).toEqual({ exitPrice: 90, outcome: "loss" });
  });
});

describe("evaluateExit — SHORT", () => {
  it("returns null when price is between TP and SL", () => {
    // SHORT: TP is below entry, SL is above entry.
    expect(evaluateExit("SHORT", 100, 90, 110)).toBeNull();
  });

  it("exits at TP when price drops to take-profit", () => {
    expect(evaluateExit("SHORT", 90, 90, 110)).toEqual({ exitPrice: 90, outcome: "win" });
  });

  it("exits at TP even when price overshoots take-profit downward", () => {
    expect(evaluateExit("SHORT", 85, 90, 110)).toEqual({ exitPrice: 90, outcome: "win" });
  });

  it("exits at SL when price rises to the stop-loss", () => {
    expect(evaluateExit("SHORT", 110, 90, 110)).toEqual({ exitPrice: 110, outcome: "loss" });
  });

  it("exits at SL even when price gaps above stop-loss", () => {
    expect(evaluateExit("SHORT", 120, 90, 110)).toEqual({ exitPrice: 110, outcome: "loss" });
  });
});
