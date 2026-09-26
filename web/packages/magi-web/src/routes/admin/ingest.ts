// BFF proxy: ingest a knowledge document (paste or uploaded text resolved to
// {title, text} client-side). Relays the admin-api status (incl. 422 unknown
// subject).

import { NextResponse } from "next/server";
import { z } from "zod";

import { ingestDocument } from "../../lib/admin-api";
import { invalidBody, readJsonBody } from "../../lib/route-body";

export async function POST(req: Request) {
  const body = await readJsonBody(
    req,
    z.object({
      title: z.string().optional(),
      text: z.string().optional(),
      subject: z.string().optional(),
      tags: z.array(z.string()).optional(),
    }),
  );
  if (!body) return invalidBody();
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
