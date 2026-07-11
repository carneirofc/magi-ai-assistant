// BFF: search every stored transcript (the same server-side archive the history
// adapter persists) for the session rail's search box. Mount in an app at
// `app/api/chat/search/route.ts`. Server-only — it reads the transcript store
// on disk; nothing leaves the BFF but session ids + short snippets.

import { NextResponse } from "next/server";

import { searchThreads } from "../../lib/chat-history-store";

export async function GET(request: Request) {
  const q = new URL(request.url).searchParams.get("q") ?? "";
  if (!q.trim()) {
    return NextResponse.json({ hits: [] });
  }
  const hits = await searchThreads(q);
  return NextResponse.json({ hits }, { headers: { "Cache-Control": "no-store" } });
}
