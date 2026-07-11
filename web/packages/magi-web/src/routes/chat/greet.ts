// BFF: ask the assistant to open the conversation (chat-api /v1/sessions/
// {id}/greet) and pipe the SSE stream back to the browser — the bearer stays
// server-side, like every chat relay. Mount in an app at
// `app/api/chat/greet/route.ts`. Greetings are text-only (the greet policy
// forbids tools/media), so the body relays straight through.

import { NextResponse } from "next/server";

import { openGreetingStream } from "../../lib/chat-api";

type Payload = { sessionId?: string; userId?: string };

export async function POST(req: Request) {
  const b = (await req.json().catch(() => ({}))) as Payload;
  const sessionId = (b.sessionId ?? "").trim();
  const userId = (b.userId ?? "").trim();
  if (!sessionId || !userId) {
    return NextResponse.json({ error: "sessionId and userId are required" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await openGreetingStream(sessionId, userId);
  } catch {
    return NextResponse.json({ error: "chat-api unreachable" }, { status: 502 });
  }
  if (!upstream.ok || !upstream.body) {
    return NextResponse.json(
      { error: `chat-api greet failed (${upstream.status})` },
      { status: 502 },
    );
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-store, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
