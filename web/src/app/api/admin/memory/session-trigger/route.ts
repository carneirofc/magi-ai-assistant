// BFF proxy: operator-triggered memory passes for one session (summarize /
// curate / flush). The upstream status is relayed verbatim — notably 503 when
// the deployment has no model wired for the model-backed passes.

import { NextResponse } from "next/server";

import { triggerSessionMemory, type MemoryTriggerAction } from "@/lib/admin-api";

type Payload = {
  userId?: string;
  sessionId?: string;
  action?: string;
};

const ACTIONS: readonly MemoryTriggerAction[] = ["summarize", "curate", "flush"];

function isAction(value: string | undefined): value is MemoryTriggerAction {
  return value !== undefined && (ACTIONS as readonly string[]).includes(value);
}

export async function POST(req: Request) {
  const b = (await req.json().catch(() => ({}))) as Payload;
  if (!b.userId || !b.sessionId || !isAction(b.action)) {
    return NextResponse.json(
      { error: "userId, sessionId and a valid action (summarize|curate|flush) required" },
      { status: 400 },
    );
  }
  const res = await triggerSessionMemory(b.userId, b.sessionId, b.action);
  const text = await res.text();
  return new NextResponse(text, {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
