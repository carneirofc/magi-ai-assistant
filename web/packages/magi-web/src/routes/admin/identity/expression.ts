// BFF: serve (GET), upload (PUT) or clear (DELETE) one mood's expression
// portrait via the admin-api. Mount in an app at
// `app/api/admin/identity/expressions/[mood]/route.ts`; the upload body carries
// base64 bytes, and every write relays the admin-api status (incl. 409 stale
// version, 422 bad image or mood key).

import { NextResponse } from "next/server";

import {
  deleteIdentityExpression,
  fetchIdentityExpression,
  putIdentityExpression,
} from "../../../lib/admin-api";

type Ctx = { params: Promise<{ mood: string }> };

export async function GET(_req: Request, ctx: Ctx) {
  const { mood } = await ctx.params;
  const res = await fetchIdentityExpression(mood);
  if (!res.ok || !res.body) {
    return new Response("not found", { status: 404 });
  }
  return new Response(res.body, {
    status: 200,
    headers: {
      "Content-Type": res.headers.get("Content-Type") ?? "application/octet-stream",
      // Busted by ?v=<per-expression version> at the call site → safe to cache hard.
      "Cache-Control": "public, max-age=31536000, immutable",
    },
  });
}

export async function PUT(req: Request, ctx: Ctx) {
  const { mood } = await ctx.params;
  const b = (await req.json().catch(() => ({}))) as {
    data_base64?: string;
    mime_type?: string;
    filename?: string;
    expectedVersion?: string;
  };
  if (!b.data_base64 || !b.mime_type) {
    return NextResponse.json(
      { error: "data_base64 and mime_type required" },
      { status: 400 },
    );
  }
  const res = await putIdentityExpression(mood, {
    dataBase64: b.data_base64,
    mimeType: b.mime_type,
    filename: b.filename,
    expectedVersion: b.expectedVersion,
  });
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}

export async function DELETE(req: Request, ctx: Ctx) {
  const { mood } = await ctx.params;
  const expectedVersion = new URL(req.url).searchParams.get("expected_version") ?? undefined;
  const res = await deleteIdentityExpression(mood, expectedVersion);
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
