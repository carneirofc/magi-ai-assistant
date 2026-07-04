// BFF: the bot's presented identity (name/description/avatar flag), read from the
// chat-api. The chat console renders this as the assistant's face + name. The
// bearer token stays server-side (chat-api.ts). Management goes through
// /api/admin/identity, not here.

import { NextResponse } from "next/server";

import { getIdentity } from "@carneirofc/magi-web/lib/chat-api";

export const dynamic = "force-dynamic";

export async function GET() {
  const identity = await getIdentity();
  if (!identity) {
    return NextResponse.json({ error: "chat-api unreachable" }, { status: 502 });
  }
  return NextResponse.json(identity, { headers: { "Cache-Control": "no-store" } });
}
