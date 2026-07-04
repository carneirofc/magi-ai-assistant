// BFF for chat transcript persistence: reads/writes one session's transcript to the
// server's temp-dir store (chat-history-store.ts) so the browser never has to hold it
// (localStorage can't fit inline image bytes). GET loads, PUT saves the whole thread,
// DELETE drops it. The client adapter lives in lib/chat-history.ts.

import { NextResponse } from "next/server";

import { deleteThread, readThread, writeThread } from "../../lib/chat-history-store";
import { offloadTranscriptMedia } from "../../lib/chat-media-offload";

// Next 15 delivers route params as a Promise; awaiting a plain object is harmless too.
type Ctx = { params: Promise<{ sessionId: string }> };

const EMPTY = { headId: null, items: [] };

export async function GET(_req: Request, { params }: Ctx) {
  const { sessionId } = await params;
  const data = await readThread(sessionId);
  return NextResponse.json(data ?? EMPTY);
}

export async function PUT(req: Request, { params }: Ctx) {
  const { sessionId } = await params;
  const body = (await req.json().catch(() => null)) as unknown;
  if (body === null || typeof body !== "object") {
    return NextResponse.json({ error: "invalid transcript body" }, { status: 400 });
  }
  // Move inline image/file bytes to the blob store; the transcript keeps only refs.
  const offloaded = await offloadTranscriptMedia(body);
  const ok = await writeThread(sessionId, offloaded);
  if (!ok) return NextResponse.json({ error: "invalid session id" }, { status: 400 });
  return NextResponse.json({ ok: true });
}

export async function DELETE(_req: Request, { params }: Ctx) {
  const { sessionId } = await params;
  await deleteThread(sessionId);
  return NextResponse.json({ ok: true });
}
