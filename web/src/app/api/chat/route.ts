// BFF proxy for the chat console: forwards one operator message to the chat-api's
// SSE stream and relays the event stream back to the browser. The bearer token
// (API_AUTH_TOKEN) is attached server-side in chat-api.ts and never reaches the
// client. This streams rather than buffering — `delta`/tool frames pass through
// verbatim as they arrive. The one exception is the terminal `done` frame: its
// reply media is offloaded to the blob store so the browser fetches images from
// the cacheable /api/chat/blobs/<id> endpoint instead of a one-shot data: URI.

import { NextResponse } from "next/server";

import { openMessageStream, type InboundAttachment } from "@/lib/chat-api";
import { offloadReplyMedia } from "@/lib/chat-media-offload";

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

/** Rewrite one SSE frame (the text between `\n\n` separators, separator re-added).
 * Only the `done` frame is touched — its reply media is offloaded to the blob
 * store; every other frame is returned verbatim so streaming stays live. A parse
 * failure falls back to the original frame untouched. */
async function rewriteFrame(frame: string): Promise<string> {
  if (!/^event:\s*done\b/m.test(frame)) return `${frame}\n\n`;
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (dataLines.length === 0) return `${frame}\n\n`;
  try {
    const data = JSON.parse(dataLines.join("\n")) as { media?: unknown };
    await offloadReplyMedia(data.media);
    return `event: done\ndata: ${JSON.stringify(data)}\n\n`;
  } catch {
    return `${frame}\n\n`;
  }
}

/** Relay the upstream SSE body, offloading the `done` frame's media in flight.
 * Frames are emitted as soon as they complete, so `delta`/tool activity keeps its
 * live cadence; only the terminal `done` frame awaits the blob store. */
function offloadingRelay(upstream: ReadableStream<Uint8Array>): ReadableStream<Uint8Array> {
  const reader = upstream.getReader();
  const decoder = new TextDecoder();
  const encoder = new TextEncoder();
  let buffer = "";

  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          buffer += decoder.decode();
          // A trailing frame without its blank-line terminator still needs relaying.
          if (buffer.length > 0) controller.enqueue(encoder.encode(await rewriteFrame(buffer)));
          controller.close();
          return;
        }
        buffer += decoder.decode(value, { stream: true });
        let sep = buffer.indexOf("\n\n");
        if (sep === -1) continue; // no complete frame yet — read more
        let out = "";
        while (sep !== -1) {
          out += await rewriteFrame(buffer.slice(0, sep));
          buffer = buffer.slice(sep + 2);
          sep = buffer.indexOf("\n\n");
        }
        controller.enqueue(encoder.encode(out));
        return;
      }
    },
    cancel() {
      void reader.cancel();
    },
  });
}

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

  return new Response(offloadingRelay(upstream.body), {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-store, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
