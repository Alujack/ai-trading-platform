import type { ErrorRequestHandler } from "express";
import { Prisma } from "@prisma/client";
import { ZodError } from "zod";
import { HttpError } from "../errors/httpError";

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export const errorHandler: ErrorRequestHandler = (err, req, res, _next) => {
  if (err instanceof HttpError) {
    const body: Record<string, unknown> = { error: err.message };
    if (err.details !== undefined) body.details = err.details;
    return res.status(err.status).json(body);
  }

  if (err instanceof ZodError) {
    return res.status(400).json({ error: "Validation failed", details: err.flatten() });
  }

  if (err instanceof Prisma.PrismaClientKnownRequestError && err.code === "P2025") {
    return res.status(404).json({ error: "Not found" });
  }

  console.error(`[error] ${req.method} ${req.originalUrl}`, err);
  return res.status(500).json({ error: "Internal server error" });
};
