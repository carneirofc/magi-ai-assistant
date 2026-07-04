// BFF proxy: save a raw memory file (persona / episodes / session files). Relays
// the admin-api status, including 409 (stale version) and 422 (invalid JSON shape).

import { NextResponse } from "next/server";

import { putRawFile } from "../../lib/admin-api";

export async function PUT(req: Request) {
  const b = (await req.json().catch(() => ({}))) as {
    kind?: string;
    content?: string;
    userId?: string;
    sessionId?: string;
    expectedVersion?: string;
  };
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
