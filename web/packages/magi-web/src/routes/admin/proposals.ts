// BFF proxy: the self-evolution proposal queue. GET lists (optionally by
// ?status=); POST decides one — body {id, action: "approve" | "reject"}.
// Status relayed verbatim: 503 = the feature is off, 409 = already decided.
// Mount at `app/api/admin/proposals/route.ts`.

import { NextResponse } from "next/server";

import { adminGet, adminRequest } from "../../lib/admin-api";

export async function GET(req: Request) {
  const status = new URL(req.url).searchParams.get("status");
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  try {
    return NextResponse.json(await adminGet(`/admin/v1/proposals${query}`));
  } catch {
    return NextResponse.json({ error: "admin-api unreachable" }, { status: 503 });
  }
}

export async function POST(req: Request) {
  const b = (await req.json().catch(() => ({}))) as { id?: string; action?: string };
  if (!b.id || (b.action !== "approve" && b.action !== "reject")) {
    return NextResponse.json(
      { error: "id and action (approve|reject) required" },
      { status: 400 },
    );
  }
  const res = await adminRequest(
    `/admin/v1/proposals/${encodeURIComponent(b.id)}/${b.action}`,
    { method: "POST" },
  );
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
