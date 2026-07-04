// BFF proxy for the chat console: forwards one operator message to the chat-api's
// SSE stream and pipes the event stream straight back to the browser. The bearer
// token (API_AUTH_TOKEN) is attached server-side in chat-api.ts and never reaches
// the client. Unlike the admin proxies this streams rather than buffering — the
// upstream body is handed through untouched so `delta`/`done` frames arrive live.

import { NextResponse } from "next/server";

import { openMessageStream, type InboundAttachment } from "@/lib/chat-api";

// Node runtime + no caching: streaming must not be collapsed into a single body.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Payload = {
  sessionId?: string;
  userId?: string;
  text?: string;
  images?: InboundAttachment[];
  files?: InboundAttachment[];
};

/** Keep only well-formed attachments (something the agent can actually load). */
function cleanAttachments(items: unknown): InboundAttachment[] {
  if (!Array.isArray(items)) return [];
  const out: InboundAttachment[] = [];
  for (const raw of items) {
    if (raw === null || typeof raw !== "object") continue;
    const a = raw as InboundAttachment;
    if (!a.url && !a.data_base64) continue;
    out.push(a);
  }
  return out;
}

export async function POST(req: Request) {
  const b = (await req.json().catch(() => ({}))) as Payload;
  const sessionId = (b.sessionId ?? "").trim();
  const userId = (b.userId ?? "").trim();
  const text = (b.text ?? "").trim();
  const images = cleanAttachments(b.images);
  const files = cleanAttachments(b.files);
  // A turn must carry something — mirrors the chat-api's own validation.
  if (!sessionId || !userId || (!text && images.length === 0 && files.length === 0)) {
    return NextResponse.json(
      { error: "sessionId, userId and text (or an attachment) are required" },
      { status: 400 },
    );
  }

  let upstream: Response;
  try {
    upstream = await openMessageStream(sessionId, { user_id: userId, text, images, files });
  } catch {
    return NextResponse.json({ error: "chat-api unreachable" }, { status: 502 });
  }
  if (!upstream.ok || !upstream.body) {
    return NextResponse.json(
      { error: `chat-api stream failed (${upstream.status})` },
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
