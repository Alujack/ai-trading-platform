import { PrismaClient } from "@prisma/client";

// Singleton — dev tooling (tsx watch) re-imports modules on reload, so cache
// the client on globalThis to avoid leaking connections each hot-reload.
const globalForPrisma = globalThis as unknown as { prisma?: PrismaClient };

export const prisma =
  globalForPrisma.prisma ??
  new PrismaClient({
    log: process.env.LOG_LEVEL === "debug" ? ["query", "warn", "error"] : ["warn", "error"],
  });

if (process.env.NODE_ENV !== "production") {
  globalForPrisma.prisma = prisma;
}
