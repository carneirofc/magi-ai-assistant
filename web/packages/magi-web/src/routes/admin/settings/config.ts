// BFF: read + update the BFF's own runtime configuration — backend URLs, bearer
// tokens, and storage locations that used to be env-only. GET returns a
// browser-safe view (secret values withheld); PUT applies a { set, clear } patch
// to the on-disk override file. See lib/runtime-config.ts. Requires the Node
// runtime (touches the filesystem). Mount at `app/api/admin/settings/config/route.ts`.

import { NextResponse } from "next/server";

import { readConfigState, writeConfig, type ConfigPatch } from "../../../lib/runtime-config";

export function GET() {
  return NextResponse.json(readConfigState(), { headers: { "Cache-Control": "no-store" } });
}

export async function PUT(req: Request) {
  const body = (await req.json().catch(() => ({}))) as ConfigPatch;
  try {
    return NextResponse.json(writeConfig({ set: body.set, clear: body.clear }));
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }
}
