import { Prisma } from "@prisma/client";

export function ser<T>(value: T): unknown {
  if (value === null || value === undefined) return value;
  if (value instanceof Prisma.Decimal) return value.toFixed();
  if (value instanceof Date) return value.toISOString();
  if (Array.isArray(value)) return value.map(ser);
  if (typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>)) {
      out[key] = ser((value as Record<string, unknown>)[key]);
    }
    return out;
  }
  return value;
}
