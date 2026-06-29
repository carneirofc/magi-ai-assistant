// BFF proxy: ingest a knowledge document (paste or uploaded text resolved to
// {title, text} client-side). Relays the admin-api status (incl. 422 unknown
// subject).

import { NextResponse } from "next/server";

import { ingestDocument } from "@/lib/admin-api";

export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as {
    title?: string;
    text?: string;
    subject?: string;
    tags?: string[];
  };
  if (!body.title || !body.text) {
    return NextResponse.json({ error: "title and text required" }, { status: 400 });
  }
  const res = await ingestDocument({
    title: body.title,
    text: body.text,
    subject: body.subject,
    tags: body.tags,
  });
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
