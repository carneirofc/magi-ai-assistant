// BFF: read + update the bot identity's name/description via the admin-api.
// The picture is managed on the /avatar sub-route. Relays the admin-api status,
// including 409 (stale version).

import { NextResponse } from "next/server";

import { getIdentity, updateIdentity } from "../../lib/admin-api";

export async function GET() {
  try {
    return NextResponse.json(await getIdentity(), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json({ error: "admin-api unreachable" }, { status: 502 });
  }
}

export async function PUT(req: Request) {
  const b = (await req.json().catch(() => ({}))) as {
    display_name?: string;
    description?: string;
    expectedVersion?: string;
  };
  const res = await updateIdentity({
    display_name: b.display_name ?? "",
    description: b.description ?? "",
    expectedVersion: b.expectedVersion,
  });
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
