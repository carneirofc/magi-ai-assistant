// Greet-on-open: the assistant speaks first when a conversation is brand new.
//
// The flow runs OUTSIDE the assistant-ui runtime (the runtime only turns on a
// user message): the console checks that the active session's stored transcript
// is empty, streams the greeting through the BFF relay (/api/chat/greet) while
// driving the shared mood/lifecycle context — so the companion stage reacts
// live — and then seeds the transcript store with the finished greeting as an
// assistant message. Remounting the thread (the console bumps its key) makes
// the history adapter load it like any other restored turn.
//
// Policy, on purpose: greet ONLY when the transcript is empty — a brand-new
// conversation (including "New chat"). Reloading or resuming an ongoing
// conversation never greets again.

import { parseFrame } from "./chat-adapter";
import type { ChatLifecycle } from "./chat-mood";

function historyUrl(sessionId: string): string {
  return `/api/chat/history/${encodeURIComponent(sessionId)}`;
}

/** Whether the session's stored transcript is empty (or absent). Errors count
 * as non-empty so a flaky store can't cause a spurious mid-conversation greet. */
export async function sessionTranscriptEmpty(sessionId: string): Promise<boolean> {
  try {
    const res = await fetch(historyUrl(sessionId), { cache: "no-store" });
    if (res.status === 404) return true;
    if (!res.ok) return false;
    const body = (await res.json()) as { items?: unknown[] } | null;
    return !body || !Array.isArray(body.items) || body.items.length === 0;
  } catch {
    return false;
  }
}

/** Seed the transcript store with the greeting as a completed assistant turn,
 * in the same stored shape the history adapter persists — so the next thread
 * mount restores it like any other message. */
async function seedGreeting(sessionId: string, text: string): Promise<void> {
  const id = `greet-${Date.now().toString(36)}`;
  const thread = {
    headId: id,
    items: [
      {
        parentId: null,
        message: {
          id,
          role: "assistant",
          content: [{ type: "text", text }],
          createdAt: new Date().toISOString(),
          status: { type: "complete", reason: "stop" },
          metadata: { unstable_state: null, unstable_annotations: [], unstable_data: [], steps: [], custom: {} },
        },
      },
    ],
  };
  await fetch(historyUrl(sessionId), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(thread),
  });
}

export type GreetingEvents = {
  onMood?: (mood: string) => void;
  onLifecycle?: (lifecycle: ChatLifecycle) => void;
};

/** Greet if (and only if) this session's transcript is empty. Streams the
 * greeting while reporting mood/lifecycle, seeds the transcript store, and
 * resolves true when a greeting landed (the caller then remounts its thread).
 * Every failure path resolves false and ends at lifecycle idle — a missing
 * greeting must never wedge the console. */
export async function greetIfFresh(
  sessionId: string,
  userId: string,
  events: GreetingEvents = {},
): Promise<boolean> {
  if (!sessionId || !userId) return false;
  if (!(await sessionTranscriptEmpty(sessionId))) return false;

  events.onLifecycle?.("thinking");
  try {
    const res = await fetch("/api/chat/greet", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId, userId }),
    });
    if (!res.ok || !res.body) {
      events.onLifecycle?.("idle");
      return false;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let chunks = "";
    let finalText = "";
    let isError = false;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep = buffer.indexOf("\n\n");
      while (sep !== -1) {
        const frame = parseFrame(buffer.slice(0, sep));
        buffer = buffer.slice(sep + 2);
        sep = buffer.indexOf("\n\n");
        if (!frame) continue;
        if (frame.event === "meta" && typeof frame.data.mood === "string" && frame.data.mood) {
          events.onMood?.(frame.data.mood);
        } else if (frame.event === "delta" && typeof frame.data.text === "string") {
          chunks += frame.data.text;
          events.onLifecycle?.("streaming");
        } else if (frame.event === "done") {
          if (typeof frame.data.text === "string") finalText = frame.data.text;
          if (typeof frame.data.mood === "string" && frame.data.mood) {
            events.onMood?.(frame.data.mood);
          }
          isError = frame.data.is_error === true;
        }
      }
    }

    const text = (finalText || chunks).trim();
    if (!text || isError) {
      events.onLifecycle?.("idle");
      return false;
    }
    // Recheck before seeding: if the user typed while the greeting streamed,
    // their turn owns the transcript — never overwrite it.
    if (!(await sessionTranscriptEmpty(sessionId))) {
      events.onLifecycle?.("idle");
      return false;
    }
    await seedGreeting(sessionId, text);
    events.onLifecycle?.("idle");
    return true;
  } catch {
    events.onLifecycle?.("idle");
    return false;
  }
}
