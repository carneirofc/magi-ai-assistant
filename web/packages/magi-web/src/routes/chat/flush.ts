// BFF: close a session server-side (chat-api /v1/sessions/{id}/flush) — the
// context inspector's "fresh session, carry the gist" action: the engine folds
// the rolling summary into an episode, so the gist survives into the next
// conversation's memory. Mount in an app at `app/api/chat/flush/route.ts`.

import { NextResponse } from "next/server";

import { flushChatSession } from "../../lib/chat-api";

export async function POST(req: Request) {
  const b = (await req.json().catch(() => ({}))) as { sessionId?: string; userId?: string };
  if (!b.sessionId || !b.userId) {
    return NextResponse.json({ error: "sessionId and userId required" }, { status: 400 });
  }
  const dropped = await flushChatSession(b.sessionId, b.userId);
  if (dropped === null) {
    return NextResponse.json({ error: "chat-api unreachable" }, { status: 503 });
  }
  return NextResponse.json({ dropped });
}
