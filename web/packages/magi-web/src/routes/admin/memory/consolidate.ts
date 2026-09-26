// BFF proxy: maintenance curation over one user's whole fact sheet (merge
// duplicates, drop contradictions/stale facts). Status relayed verbatim —
// 503 when the deployment has no model wired. Mount at
// `app/api/admin/memory/consolidate/route.ts`.

import { NextResponse } from "next/server";
import { z } from "zod";

import { consolidateFacts } from "../../../lib/admin-api";
import { invalidBody, readJsonBody } from "../../../lib/route-body";

export async function POST(req: Request) {
  const b = await readJsonBody(req, z.object({ userId: z.string().optional() }));
  if (!b) return invalidBody();
  if (!b.userId) {
    return NextResponse.json({ error: "userId required" }, { status: 400 });
  }
  const res = await consolidateFacts(b.userId);
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
