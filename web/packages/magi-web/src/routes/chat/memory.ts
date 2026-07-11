// BFF: relay the requesting user's durable facts from the chat-api
// (/v1/memory/facts) for the companion's ambient memory panel. Mount in an app
// at `app/api/memory/route.ts`. The bearer stays server-side, like every chat
// relay; the app decides which user id to ask for (the companion passes its
// pinned user).

import { NextResponse } from "next/server";

import { getSelfMemory } from "../../lib/chat-api";

export async function GET(request: Request) {
  const userId = new URL(request.url).searchParams.get("user_id") ?? "";
  if (!userId) {
    return NextResponse.json({ error: "user_id required" }, { status: 400 });
  }
  const facts = await getSelfMemory(userId);
  if (facts === null) {
    return NextResponse.json({ error: "chat api unreachable" }, { status: 502 });
  }
  return NextResponse.json({ facts }, { headers: { "Cache-Control": "no-store" } });
}
