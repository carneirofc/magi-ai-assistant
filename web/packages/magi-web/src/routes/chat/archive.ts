// BFF: relay the engine's server-side history search (chat-api
// /v1/sessions/search) — transcripts, session summaries, and episodes for one
// user. Backs the composer's "reference a previous chat" picker; distinct from
// /api/chat/search, which scans the WEB-side transcript store (what the user
// saw, incl. media) for the session rail. Mount at `app/api/chat/archive/route.ts`.

import { NextResponse } from "next/server";

import { searchChatArchive } from "../../lib/chat-api";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const q = (url.searchParams.get("q") ?? "").trim();
  const userId = url.searchParams.get("userId") ?? "";
  const limit = Math.min(Math.max(Number(url.searchParams.get("limit")) || 8, 1), 50);
  if (!q || !userId) {
    return NextResponse.json({ error: "q and userId required" }, { status: 400 });
  }
  const hits = await searchChatArchive(q, userId, limit);
  if (hits === null) {
    return NextResponse.json({ error: "chat-api unreachable" }, { status: 503 });
  }
  return NextResponse.json({ hits });
}
