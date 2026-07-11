// BFF proxy: the git history of one raw memory file (memory_git_enabled), and
// — with `sha` — that version's content. Empty entries mean versioning is off;
// the drawer hides itself. Mount at `app/api/admin/memory/file-history/route.ts`.

import { NextResponse } from "next/server";

import { getRawFileHistory, getRawFileVersion } from "../../../lib/admin-api";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const kind = url.searchParams.get("kind") ?? "";
  if (!kind) {
    return NextResponse.json({ error: "kind required" }, { status: 400 });
  }
  const opts = {
    userId: url.searchParams.get("userId") ?? undefined,
    sessionId: url.searchParams.get("sessionId") ?? undefined,
  };
  const sha = url.searchParams.get("sha");
  try {
    if (sha) {
      const res = await getRawFileVersion(kind, sha, opts);
      return new NextResponse(await res.text(), {
        status: res.status,
        headers: { "Content-Type": "application/json" },
      });
    }
    const limit = Number(url.searchParams.get("limit")) || undefined;
    return NextResponse.json(await getRawFileHistory(kind, { ...opts, limit }));
  } catch {
    return NextResponse.json({ error: "admin-api unreachable" }, { status: 503 });
  }
}
