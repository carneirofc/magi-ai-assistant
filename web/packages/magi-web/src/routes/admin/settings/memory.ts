// BFF: read + update the operator memory settings (location + git-versioning) via
// the admin-api. These apply on the next restart of the service; the response carries
// `restart_required` so the UI can say so. Relays the admin-api status, including 409
// (stale version) and 503 (settings store not wired).

import { NextResponse } from "next/server";

import { getMemorySettings, updateMemorySettings } from "../../../lib/admin-api";

export async function GET() {
  try {
    return NextResponse.json(await getMemorySettings(), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json({ error: "admin-api unreachable" }, { status: 502 });
  }
}

export async function PUT(req: Request) {
  const b = (await req.json().catch(() => ({}))) as {
    memory_dir?: string;
    git_enabled?: boolean;
    git_author_name?: string;
    git_author_email?: string;
    expectedVersion?: string;
  };
  const res = await updateMemorySettings({
    memory_dir: b.memory_dir ?? "",
    git_enabled: b.git_enabled ?? false,
    git_author_name: b.git_author_name ?? "",
    git_author_email: b.git_author_email ?? "",
    expectedVersion: b.expectedVersion,
  });
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
