// BFF: relay speech transcription (chat-api /v1/stt → the whisper-class
// sidecar). Mount in an app at `app/api/chat/stt/route.ts`. Takes the
// browser's multipart recording as-is and forwards it; the reply is
// `{text, language?, duration?}`. 503 = no sidecar wired — the composer
// surfaces its dictation hint and the typed text stands.

import { NextResponse } from "next/server";

import { forwardTranscription } from "../../lib/chat-api";

export async function POST(req: Request) {
  let form: FormData;
  try {
    form = await req.formData();
  } catch {
    return NextResponse.json({ error: "multipart form with a `file` required" }, { status: 400 });
  }
  const file = form.get("file");
  if (!(file instanceof Blob) || file.size === 0) {
    return NextResponse.json({ error: "empty or missing `file`" }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await forwardTranscription(form);
  } catch {
    return NextResponse.json({ error: "chat-api unreachable" }, { status: 502 });
  }
  const body = await upstream.text().catch(() => "");
  return new Response(body, {
    status: upstream.status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}
