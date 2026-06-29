// BFF proxy: create a subject. Session already verified by middleware.

import { NextResponse } from "next/server";

import { createSubject } from "@/lib/admin-api";

export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as { name?: string; description?: string };
  const res = await createSubject(body.name ?? "", body.description ?? "");
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
