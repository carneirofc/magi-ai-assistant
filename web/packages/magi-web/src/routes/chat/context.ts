// BFF: relay the engine's context accounting (chat-api /v1/sessions/{id}/context)
// for the composer's context inspector. Mount in an app at
// `app/api/chat/context/route.ts`. Null-safe: an unreachable engine answers 503
// and the inspector stays quiet.

import { NextResponse } from "next/server";

import { getContextStats } from "../../lib/chat-api";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const sessionId = url.searchParams.get("sessionId") ?? "";
  const userId = url.searchParams.get("userId") ?? "";
  if (!sessionId || !userId) {
    return NextResponse.json({ error: "sessionId and userId required" }, { status: 400 });
  }
  const stats = await getContextStats(sessionId, userId);
  if (!stats) {
    return NextResponse.json({ error: "chat-api unreachable" }, { status: 503 });
  }
  return NextResponse.json(stats);
}
