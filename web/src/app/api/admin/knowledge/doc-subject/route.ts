// BFF proxy: set a document's subject. doc_id travels in the body to avoid
// nesting under the catch-all document route.

import { NextResponse } from "next/server";

import { setDocumentSubject } from "@carneirofc/magi-web/lib/admin-api";

export async function PUT(req: Request) {
  const body = (await req.json().catch(() => ({}))) as { docId?: string; subject?: string };
  if (!body.docId) return NextResponse.json({ error: "docId required" }, { status: 400 });
  const res = await setDocumentSubject(body.docId, body.subject ?? "");
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
