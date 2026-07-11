// BFF: relay the auto-title pass (chat-api /v1/title) — a short model-made
// title for a conversation's opening exchange. Mount in an app at
// `app/api/chat/title/route.ts`. Null/unavailable means the client keeps its
// derived title, so this route never errors the console.

import { NextResponse } from "next/server";

import { requestTitle } from "../../lib/chat-api";

export async function POST(req: Request) {
  const b = (await req.json().catch(() => ({}))) as { text?: string };
  const text = (b.text ?? "").trim();
  if (!text) {
    return NextResponse.json({ error: "text required" }, { status: 400 });
  }
  return NextResponse.json({ title: await requestTitle(text) });
}
