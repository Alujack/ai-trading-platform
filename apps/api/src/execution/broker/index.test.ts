import { afterEach, describe, expect, it } from "vitest";
import { __resetBroker, getBroker } from "./index";
import { ExnessBroker } from "./exnessBroker";
import { PaperBroker } from "./paperBroker";

const ENV_KEYS = ["BROKER", "EXNESS_ENV", "MT5_BRIDGE_URL", "MT5_BRIDGE_TOKEN"];

afterEach(() => {
  for (const k of ENV_KEYS) delete process.env[k];
  __resetBroker();
});

describe("getBroker factory", () => {
  it("defaults to PaperBroker when BROKER is unset", () => {
    expect(getBroker()).toBeInstanceOf(PaperBroker);
  });

  it("returns a PaperBroker for BROKER=paper", () => {
    process.env.BROKER = "paper";
    __resetBroker();
    expect(getBroker().name).toBe("paper");
  });

  it("returns an ExnessBroker (demo) when configured", () => {
    process.env.BROKER = "exness";
    process.env.MT5_BRIDGE_URL = "http://bridge:8800";
    process.env.MT5_BRIDGE_TOKEN = "secret";
    __resetBroker();
    const b = getBroker();
    expect(b).toBeInstanceOf(ExnessBroker);
    expect(b.name).toBe("exness_demo");
  });

  it("labels real when EXNESS_ENV=real", () => {
    process.env.BROKER = "exness";
    process.env.EXNESS_ENV = "real";
    process.env.MT5_BRIDGE_URL = "http://bridge:8800";
    process.env.MT5_BRIDGE_TOKEN = "secret";
    __resetBroker();
    expect(getBroker().name).toBe("exness_real");
  });

  it("throws when BROKER=exness without bridge URL/token", () => {
    process.env.BROKER = "exness";
    __resetBroker();
    expect(() => getBroker()).toThrow(/MT5_BRIDGE_URL/);
  });

  it("throws on an unknown BROKER value", () => {
    process.env.BROKER = "robinhood";
    __resetBroker();
    expect(() => getBroker()).toThrow(/unknown BROKER/);
  });

  it("memoizes — repeated calls return the same instance", () => {
    const a = getBroker();
    const b = getBroker();
    expect(a).toBe(b);
  });
});
