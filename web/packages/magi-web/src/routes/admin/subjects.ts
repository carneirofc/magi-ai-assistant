// BFF proxy: create a subject. Session already verified by middleware.

import { NextResponse } from "next/server";
import { z } from "zod";

import { createSubject } from "../../lib/admin-api";
import { invalidBody, readJsonBody } from "../../lib/route-body";

export async function POST(req: Request) {
  const body = await readJsonBody(
    req,
    z.object({ name: z.string().optional(), description: z.string().optional() }),
  );
  if (!body) return invalidBody();
  const res = await createSubject(body.name ?? "", body.description ?? "");
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
