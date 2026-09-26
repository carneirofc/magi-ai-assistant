// BFF proxy: save a raw memory file (persona / episodes / session files). Relays
// the admin-api status, including 409 (stale version) and 422 (invalid JSON shape).

import { NextResponse } from "next/server";
import { z } from "zod";

import { putRawFile } from "../../lib/admin-api";
import { invalidBody, readJsonBody } from "../../lib/route-body";

export async function PUT(req: Request) {
  const b = await readJsonBody(
    req,
    z.object({
      kind: z.string().optional(),
      content: z.string().optional(),
      userId: z.string().optional(),
      sessionId: z.string().optional(),
      expectedVersion: z.string().optional(),
    }),
  );
  if (!b) return invalidBody();
  if (!b.kind || b.content === undefined) {
    return NextResponse.json({ error: "kind and content required" }, { status: 400 });
  }
  const res = await putRawFile(b.kind, b.content, {
    userId: b.userId,
    sessionId: b.sessionId,
    expectedVersion: b.expectedVersion,
  });
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
