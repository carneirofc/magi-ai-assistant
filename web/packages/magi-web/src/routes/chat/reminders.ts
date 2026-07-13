// BFF: relay the user's reminders (chat-api /v1/reminders) for the companion's
// upcoming strip. Feature off = empty list, so the panel hides itself. Mount at
// `app/api/chat/reminders/route.ts`.

import { NextResponse } from "next/server";

import { getConfigValue } from "../../lib/runtime-config";

async function fetchReminders(userId: string): Promise<Response> {
  const base = getConfigValue("chatApiUrl");
  const token = getConfigValue("apiAuthToken");
  return fetch(`${base}/v1/reminders?user_id=${encodeURIComponent(userId)}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    cache: "no-store",
  });
}

export async function GET(req: Request) {
  const userId = new URL(req.url).searchParams.get("userId") ?? "";
  if (!userId) {
    return NextResponse.json({ error: "userId required" }, { status: 400 });
  }
  try {
    const res = await fetchReminders(userId);
    if (!res.ok) return NextResponse.json({ reminders: [] });
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json({ reminders: [] });
  }
}
