// BFF proxy: add/remove a document's tags. doc_id in the body.

import { NextResponse } from "next/server";

import { editDocumentTags } from "../../../lib/admin-api";

export async function PATCH(req: Request) {
  const body = (await req.json().catch(() => ({}))) as {
    docId?: string;
    add?: string[];
    remove?: string[];
  };
  if (!body.docId) return NextResponse.json({ error: "docId required" }, { status: 400 });
  const res = await editDocumentTags(body.docId, { add: body.add, remove: body.remove });
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
