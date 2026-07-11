// BFF: relay TTS synthesis (chat-api /v1/tts → the TTS sidecar). Mount in an
// app at `app/api/chat/tts/route.ts`. The audio comes back with its real mime;
// a 503 means no sidecar is wired and the client stays silent — voice only
// ever adds to the text already on screen.

import { NextResponse } from "next/server";

import { openTtsAudio } from "../../lib/chat-api";

export async function POST(req: Request) {
  const b = (await req.json().catch(() => ({}))) as { text?: string; mood?: string | null };
  const text = (b.text ?? "").trim();
  if (!text) {
    return NextResponse.json({ error: "text required" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await openTtsAudio(text, b.mood ?? null);
  } catch {
    return NextResponse.json({ error: "chat-api unreachable" }, { status: 502 });
  }
  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => "");
    return NextResponse.json(
      { error: detail || `tts failed (${upstream.status})` },
      { status: upstream.status },
    );
  }
  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "audio/mpeg",
      "Cache-Control": "no-store",
    },
  });
}
