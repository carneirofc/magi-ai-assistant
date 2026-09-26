// BFF: read + update the operator memory settings (location + git-versioning) via
// the admin-api. These apply on the next restart of the service; the response carries
// `restart_required` so the UI can say so. Relays the admin-api status, including 409
// (stale version) and 503 (settings store not wired).

import { NextResponse } from "next/server";
import { z } from "zod";

import { getMemorySettings, updateMemorySettings } from "../../../lib/admin-api";
import { invalidBody, readJsonBody } from "../../../lib/route-body";

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
  const b = await readJsonBody(
    req,
    z.object({
      memory_dir: z.string().optional(),
      git_enabled: z.boolean().optional(),
      git_author_name: z.string().optional(),
      git_author_email: z.string().optional(),
      expectedVersion: z.string().optional(),
    }),
  );
  if (!b) return invalidBody();
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
