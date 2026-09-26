// BFF proxy: add/remove a document's tags. doc_id in the body.

import { NextResponse } from "next/server";
import { z } from "zod";

import { editDocumentTags } from "../../../lib/admin-api";
import { invalidBody, readJsonBody } from "../../../lib/route-body";

export async function PATCH(req: Request) {
  const body = await readJsonBody(
    req,
    z.object({
      docId: z.string().optional(),
      add: z.array(z.string()).optional(),
      remove: z.array(z.string()).optional(),
    }),
  );
  if (!body) return invalidBody();
  if (!body.docId) return NextResponse.json({ error: "docId required" }, { status: 400 });
  const res = await editDocumentTags(body.docId, { add: body.add, remove: body.remove });
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
