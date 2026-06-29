// BFF proxy: the browser-facing endpoint for the knowledge document list. The
// middleware has already verified the session, so this just forwards to the
// admin-api with the server-side bearer and relays the JSON. The browser never
// sees ADMIN_AUTH_TOKEN or the admin-api URL.
//
// Slice 1's list page is a server component that calls the admin-api directly, so
// it doesn't need this route — it exists as the seam client components and future
// mutations (rename/tag/delete) will use.

import { NextResponse } from "next/server";

import { listKnowledgeDocuments } from "@/lib/admin-api";

export async function GET() {
  try {
    return NextResponse.json(await listKnowledgeDocuments());
  } catch {
    return NextResponse.json({ error: "admin-api unavailable" }, { status: 502 });
  }
}
