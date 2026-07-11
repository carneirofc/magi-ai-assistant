// BFF proxy: dry-run the context assembly for a query — exactly what each
// memory section would inject (the retrieval-quality lens). Mount at
// `app/api/admin/memory/recall-preview/route.ts`.

import { NextResponse } from "next/server";

import { getRecallPreview } from "../../../lib/admin-api";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const userId = url.searchParams.get("userId") ?? "";
  const q = (url.searchParams.get("q") ?? "").trim();
  if (!userId || !q) {
    return NextResponse.json({ error: "userId and q required" }, { status: 400 });
  }
  try {
    return NextResponse.json(await getRecallPreview(userId, q));
  } catch {
    return NextResponse.json({ error: "admin-api unreachable" }, { status: 503 });
  }
}
