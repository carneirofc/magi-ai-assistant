// BFF proxy for document mutations (rename, delete). The middleware has already
// verified the session; these forward to the admin-api with the server-side
// bearer and relay its status. The browser never sees the token.

import { NextResponse } from "next/server";
import { z } from "zod";

import { deleteDocument, renameDocument } from "../../../../lib/admin-api";
import { invalidBody, readJsonBody } from "../../../../lib/route-body";

function docIdOf(parts: string[]): string {
  return parts.map(decodeURIComponent).join("/");
}

export async function PATCH(req: Request, { params }: { params: Promise<{ docId: string[] }> }) {
  const { docId } = await params;
  const body = await readJsonBody(req, z.object({ title: z.string().optional() }));
  if (!body) return invalidBody();
  if (!body.title) {
    return NextResponse.json({ error: "title required" }, { status: 400 });
  }
  const res = await renameDocument(docIdOf(docId), body.title);
  const text = await res.text();
  return new NextResponse(text, {
    status: res.status,
    headers: { "Content-Type": res.headers.get("Content-Type") ?? "application/json" },
  });
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ docId: string[] }> }) {
  const { docId } = await params;
  const res = await deleteDocument(docIdOf(docId));
  return new NextResponse(null, { status: res.status });
}
