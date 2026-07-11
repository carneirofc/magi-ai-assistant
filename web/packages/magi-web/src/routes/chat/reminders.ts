// BFF: relay the user's reminders (chat-api /v1/reminders) for the companion's
// upcoming strip. Feature off = empty list, so the panel hides itself. Mount at
// `app/api/chat/reminders/route.ts`.

import { NextResponse } from "next/server";

async function fetchReminders(userId: string): Promise<Response> {
  const base = process.env.CHAT_API_URL ?? "http://127.0.0.1:8000";
  const token = process.env.API_AUTH_TOKEN;
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
