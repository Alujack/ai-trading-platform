import { describe, expect, it } from "vitest";
import {
  calculatePositionSize,
  checkDailyLoss,
  checkMaxDrawdown,
  isNewsWindow,
  validateRiskReward,
  type NewsLite,
} from "./riskEngine";

describe("calculatePositionSize", () => {
  it("computes lot size from risk amount divided by stop distance", () => {
    const { lotSize, riskAmount } = calculatePositionSize(10_000, 1, 100, 98);
    expect(riskAmount).toBe(100);
    expect(lotSize).toBe(50);
  });

  it("uses absolute distance regardless of stop direction", () => {
    const long = calculatePositionSize(10_000, 1, 100, 98);
    const short = calculatePositionSize(10_000, 1, 100, 102);
    expect(short.lotSize).toBe(long.lotSize);
  });

  it("scales with risk percent", () => {
    const onePct = calculatePositionSize(10_000, 1, 100, 95);
    const twoPct = calculatePositionSize(10_000, 2, 100, 95);
    expect(twoPct.lotSize).toBeCloseTo(onePct.lotSize * 2, 10);
  });

  it("throws when entry equals stop loss", () => {
    expect(() => calculatePositionSize(10_000, 1, 100, 100)).toThrow();
  });

  it("throws on non-positive balance", () => {
    expect(() => calculatePositionSize(0, 1, 100, 98)).toThrow();
    expect(() => calculatePositionSize(-1, 1, 100, 98)).toThrow();
  });

  it("throws on non-positive risk percent", () => {
    expect(() => calculatePositionSize(10_000, 0, 100, 98)).toThrow();
  });
});

describe("checkDailyLoss", () => {
  it("allows when loss is below 3% limit", () => {
    expect(checkDailyLoss("u1", 100, 10_000)).toEqual({ allowed: true });
  });

  it("allows when loss is exactly 3% (spec says > 3% trips)", () => {
    // Per spec wording "if todayLoss > 3% of balance: not allowed".
    // Exactly 3% should still be allowed.
    expect(checkDailyLoss("u1", 300, 10_000)).toEqual({ allowed: true });
  });

  it("blocks when loss exceeds 3%", () => {
    const r = checkDailyLoss("u1", 301, 10_000);
    expect(r.allowed).toBe(false);
    if (!r.allowed) expect(r.reason).toBe("Daily loss limit reached");
  });

  it("respects a custom limit percent", () => {
    expect(checkDailyLoss("u1", 200, 10_000, 1)).toEqual({
      allowed: false,
      reason: "Daily loss limit reached",
    });
    expect(checkDailyLoss("u1", 99, 10_000, 1)).toEqual({ allowed: true });
  });
});

describe("checkMaxDrawdown", () => {
  it("allows when drawdown is below 10%", () => {
    expect(checkMaxDrawdown(10_000, 9_500)).toEqual({ allowed: true });
  });

  it("allows when drawdown is exactly 10% (spec says > 10% trips)", () => {
    expect(checkMaxDrawdown(10_000, 9_000)).toEqual({ allowed: true });
  });

  it("blocks when drawdown exceeds 10%", () => {
    const r = checkMaxDrawdown(10_000, 8_999);
    expect(r.allowed).toBe(false);
    if (!r.allowed) expect(r.reason).toBe("Max drawdown exceeded");
  });

  it("rejects an invalid peak balance", () => {
    expect(checkMaxDrawdown(0, 1_000).allowed).toBe(false);
    expect(checkMaxDrawdown(-1, 1_000).allowed).toBe(false);
  });
});

describe("validateRiskReward", () => {
  it("accepts a clean 1:2 RR LONG setup", () => {
    const r = validateRiskReward(100, 98, 104);
    expect(r.rr).toBe(2);
    expect(r.acceptable).toBe(true);
  });

  it("accepts a clean 1:2 RR SHORT setup (works on abs distances)", () => {
    const r = validateRiskReward(100, 102, 96);
    expect(r.rr).toBe(2);
    expect(r.acceptable).toBe(true);
  });

  it("rejects RR just below the 2.0 threshold", () => {
    const r = validateRiskReward(100, 98, 103.99);
    expect(r.acceptable).toBe(false);
  });

  it("handles zero risk gracefully", () => {
    expect(validateRiskReward(100, 100, 104)).toEqual({ rr: 0, acceptable: false });
  });

  it("respects a custom min RR", () => {
    expect(validateRiskReward(100, 99, 102, 3).acceptable).toBe(false);
    expect(validateRiskReward(100, 99, 103, 3).acceptable).toBe(true);
  });
});

describe("isNewsWindow", () => {
  const now = new Date("2026-05-17T12:00:00Z");

  it("returns safe with no events", () => {
    expect(isNewsWindow([], 30, 30, now)).toEqual({ safe: true, nearestEvent: null });
  });

  it("ignores LOW and MEDIUM impact events even if near", () => {
    const news: NewsLite[] = [
      { title: "Minor", impact: "LOW", scheduledAt: new Date("2026-05-17T12:05:00Z") },
      { title: "Mid", impact: "MEDIUM", scheduledAt: new Date("2026-05-17T12:10:00Z") },
    ];
    const r = isNewsWindow(news, 30, 30, now);
    expect(r.safe).toBe(true);
    expect(r.nearestEvent).toBeNull();
  });

  it("flags a HIGH impact event 15 min in the future as unsafe", () => {
    const news: NewsLite[] = [
      { title: "CPI", impact: "HIGH", scheduledAt: new Date("2026-05-17T12:15:00Z") },
    ];
    const r = isNewsWindow(news, 30, 30, now);
    expect(r.safe).toBe(false);
    expect(r.nearestEvent).toBe("CPI");
  });

  it("treats an event just outside the window as safe, but still reports nearest", () => {
    const news: NewsLite[] = [
      { title: "NFP", impact: "HIGH", scheduledAt: new Date("2026-05-17T12:31:00Z") },
    ];
    const r = isNewsWindow(news, 30, 30, now);
    expect(r.safe).toBe(true);
    expect(r.nearestEvent).toBe("NFP");
  });

  it("flags an event 20 min in the past as unsafe (in -minutesAfter window)", () => {
    const news: NewsLite[] = [
      { title: "FOMC", impact: "HIGH", scheduledAt: new Date("2026-05-17T11:40:00Z") },
    ];
    expect(isNewsWindow(news, 30, 30, now).safe).toBe(false);
  });

  it("treats an event 31 min in the past as safe", () => {
    const news: NewsLite[] = [
      { title: "Old", impact: "HIGH", scheduledAt: new Date("2026-05-17T11:29:00Z") },
    ];
    expect(isNewsWindow(news, 30, 30, now).safe).toBe(true);
  });

  it("returns the nearest event title across multiple HIGH events", () => {
    const news: NewsLite[] = [
      { title: "Far", impact: "HIGH", scheduledAt: new Date("2026-05-17T14:00:00Z") },
      { title: "Near", impact: "HIGH", scheduledAt: new Date("2026-05-17T12:45:00Z") },
    ];
    const r = isNewsWindow(news, 30, 30, now);
    expect(r.nearestEvent).toBe("Near");
  });

  it("accepts ISO string timestamps", () => {
    const news: NewsLite[] = [
      { title: "CPI", impact: "HIGH", scheduledAt: "2026-05-17T12:15:00Z" },
    ];
    expect(isNewsWindow(news, 30, 30, now).safe).toBe(false);
  });
});
