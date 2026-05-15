import type { RequestHandler } from "express";
import type { ZodSchema } from "zod";
import { HttpError } from "../errors/httpError";

type Source = "query" | "params" | "body";

export const validate =
  <T>(schema: ZodSchema<T>, source: Source = "query"): RequestHandler =>
  (req, _res, next) => {
    const parsed = schema.safeParse(req[source]);
    if (!parsed.success) {
      return next(new HttpError(400, "Validation failed", parsed.error.flatten()));
    }
    (req as unknown as Record<Source, T>)[source] = parsed.data;
    next();
  };
