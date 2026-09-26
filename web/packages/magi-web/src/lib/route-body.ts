// Request-body validation for the BFF route handlers. A body that is missing,
// not JSON, or the wrong shape yields null so the handler answers 400 instead
// of letting a zod error surface as a 500.

import { NextResponse } from "next/server";
import type { z } from "zod";

/** The request's JSON body validated against `schema`; null when the body is
 * absent/malformed or doesn't match. A missing body validates as `{}`. */
export async function readJsonBody<T>(req: Request, schema: z.ZodType<T>): Promise<T | null> {
  const raw: unknown = await req.json().catch(() => ({}));
  const parsed = schema.safeParse(raw);
  return parsed.success ? parsed.data : null;
}

/** The 400 answer for a body `readJsonBody` rejected. */
export function invalidBody(): NextResponse {
  return NextResponse.json({ error: "invalid request body" }, { status: 400 });
}
