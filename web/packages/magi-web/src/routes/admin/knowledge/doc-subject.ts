// BFF proxy: set a document's subject. doc_id travels in the body to avoid
// nesting under the catch-all document route.

import { NextResponse } from "next/server";
import { z } from "zod";

import { setDocumentSubject } from "../../../lib/admin-api";
import { invalidBody, readJsonBody } from "../../../lib/route-body";

export async function PUT(req: Request) {
  const body = await readJsonBody(
    req,
    z.object({ docId: z.string().optional(), subject: z.string().optional() }),
  );
  if (!body) return invalidBody();
  if (!body.docId) return NextResponse.json({ error: "docId required" }, { status: 400 });
  const res = await setDocumentSubject(body.docId, body.subject ?? "");
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
