// BFF proxy: edit / delete a subject.

import { NextResponse } from "next/server";
import { z } from "zod";

import { deleteSubject, editSubject } from "../../../lib/admin-api";
import { invalidBody, readJsonBody } from "../../../lib/route-body";

export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await readJsonBody(
    req,
    z.object({ name: z.string().optional(), description: z.string().optional() }),
  );
  if (!body) return invalidBody();
  const res = await editSubject(id, body);
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const res = await deleteSubject(id);
  return new NextResponse(null, { status: res.status });
}
