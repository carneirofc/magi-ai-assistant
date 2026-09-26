import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import type { z } from "zod";

/** Merge conditional class names, de-duplicating conflicting Tailwind utilities.
 * The `cn(...)` helper the shadcn-derived components expect (see
 * components/ui/tooltip.tsx, components/assistant-ui/context-display.tsx). */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Parse a fetch `Response` body as JSON and validate it against `schema`.
 * Throws `Error(String(res.status))` on a non-2xx response (matching the
 * existing `` `${res.status}` `` error style across the codebase), and throws
 * the zod validation error when the body doesn't match `schema`. */
export async function fetchJson<T>(res: Response, schema: z.ZodType<T>): Promise<T> {
  if (!res.ok) throw new Error(`${res.status}`);
  return schema.parse(await res.json());
}
