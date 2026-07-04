// BFF: upload (PUT) or clear (DELETE) the bot's profile picture via the admin-api.
// The upload body carries base64 bytes; both relay the admin-api status (incl. 409
// stale version, 422 bad image).

import { NextResponse } from "next/server";

import { deleteIdentityAvatar, fetchIdentityAvatar, putIdentityAvatar } from "../../../lib/admin-api";

export async function GET() {
  const res = await fetchIdentityAvatar();
  if (!res.ok || !res.body) {
    return new Response("not found", { status: 404 });
  }
  return new Response(res.body, {
    status: 200,
    headers: {
      "Content-Type": res.headers.get("Content-Type") ?? "application/octet-stream",
      // Busted by ?v=<version> at the call site → safe to cache hard.
      "Cache-Control": "public, max-age=31536000, immutable",
    },
  });
}

export async function PUT(req: Request) {
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
  const res = await putIdentityAvatar({
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

export async function DELETE(req: Request) {
  const expectedVersion = new URL(req.url).searchParams.get("expected_version") ?? undefined;
  const res = await deleteIdentityAvatar(expectedVersion);
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
