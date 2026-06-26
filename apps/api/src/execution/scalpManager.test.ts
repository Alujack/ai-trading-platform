import { describe, expect, it } from "vitest";
import {
  decideScalpAction,
  type ScalpManageConfig,
  type TicketState,
} from "./scalpDecision";

const CFG: ScalpManageConfig = {
  minStopRatio: 0.5,
  emergencyR: 0.8,
  watchR: 0.5,
  trailStartR: 1.0,
  trailGivebackR: 0.5,
};

// Safe, non-triggering stop distances for cases not about slippage.
const SAFE = { intendedStopDist: 10, actualStopDist: 10 };

describe("decideScalpAction — unsafe stop (slippage)", () => {
  it("closes on first sight when the fill ate >half the intended stop room", () => {
    const d = decideScalpAction(
      { state: undefined, r: 0, intendedStopDist: 10, actualStopDist: 3 },
      CFG,
    );
    expect(d.action).toBe("close");
    expect(d.reason).toBe("unsafe_stop_slippage");
  });

  it("does NOT re-trigger unsafe_stop after the first check", () => {
    const state: TicketState = { checks: 1, lastR: 0, bestR: 0 };
    const d = decideScalpAction({ state, r: 0, intendedStopDist: 10, actualStopDist: 3 }, CFG);
    expect(d.action).toBe("hold");
  });

  it("holds on first sight when the stop survived the fill", () => {
    const d = decideScalpAction(
      { state: undefined, r: -0.2, intendedStopDist: 10, actualStopDist: 9 },
      CFG,
    );
    expect(d.action).toBe("hold");
    expect(d.nextState).toEqual({ checks: 1, lastR: -0.2, bestR: -0.2 });
  });
});

describe("decideScalpAction — adverse", () => {
  it("emergency-closes at R <= -emergencyR in a single check", () => {
    const d = decideScalpAction({ state: undefined, r: -0.9, ...SAFE }, CFG);
    expect(d.action).toBe("close");
    expect(d.reason).toBe("emergency_adverse");
  });

  it("two-check closes when this check is worse than the last and R <= -watchR", () => {
    const state: TicketState = { checks: 1, lastR: -0.4, bestR: 0 };
    const d = decideScalpAction({ state, r: -0.6, ...SAFE }, CFG);
    expect(d.action).toBe("close");
    expect(d.reason).toBe("two_check_adverse");
  });

  it("does not two-check close when P&L improved (recovery)", () => {
    const state: TicketState = { checks: 1, lastR: -0.6, bestR: -0.2 };
    const d = decideScalpAction({ state, r: -0.4, ...SAFE }, CFG); // improved vs -0.6
    expect(d.action).toBe("hold");
  });

  it("does not two-check close on a small adverse dip above the watch band", () => {
    const state: TicketState = { checks: 1, lastR: -0.2, bestR: 0 };
    const d = decideScalpAction({ state, r: -0.4, ...SAFE }, CFG); // worse but > -0.5
    expect(d.action).toBe("hold");
  });
});

describe("decideScalpAction — profit lock", () => {
  it("locks profit once armed (>= trailStartR) and price gives back trailGivebackR", () => {
    const state: TicketState = { checks: 3, lastR: 1.2, bestR: 1.2 };
    const d = decideScalpAction({ state, r: 0.6, ...SAFE }, CFG); // 0.6 <= 1.2 - 0.5
    expect(d.action).toBe("close");
    expect(d.reason).toBe("profit_lock");
  });

  it("does not lock when best R never reached trailStartR", () => {
    const state: TicketState = { checks: 2, lastR: 0.8, bestR: 0.8 };
    const d = decideScalpAction({ state, r: 0.2, ...SAFE }, CFG);
    expect(d.action).toBe("hold");
  });
});

describe("decideScalpAction — state tracking", () => {
  it("carries the running max R and the latest R forward", () => {
    const state: TicketState = { checks: 2, lastR: 0.3, bestR: 0.9 };
    const d = decideScalpAction({ state, r: 0.4, ...SAFE }, CFG);
    expect(d.nextState).toEqual({ checks: 3, lastR: 0.4, bestR: 0.9 });
  });
});
