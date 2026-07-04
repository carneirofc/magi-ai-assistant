// Server-only client for the Python chat-api (channels/api.py). Unlike admin-api.ts
// this reads a DIFFERENT upstream — the running brain — to introspect its live
// team/tools/MCP roster (read-only). The bearer token (API_AUTH_TOKEN, matching
// the chat-api's) lives here on the server and NEVER reaches the browser; import
// this only from server components or route handlers.

import "server-only";

import type { TeamSnapshot } from "./introspection-types";

function baseUrl(): string {
  // In docker-compose this is the chat-api service name; for local dev it's the
  // host/port `python main.py api` binds (see main.py configure_api).
  return process.env.CHAT_API_URL ?? "http://127.0.0.1:8000";
}

function authHeaders(): Record<string, string> {
  const token = process.env.API_AUTH_TOKEN;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** The live team/tools/MCP snapshot. Throws on non-2xx so the page can show an
 * error state. */
export async function getIntrospection(): Promise<TeamSnapshot> {
  const res = await fetch(`${baseUrl()}/v1/introspection`, {
    headers: authHeaders(),
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`chat-api GET /v1/introspection failed: ${res.status}`);
  }
  return (await res.json()) as TeamSnapshot;
}

/** An attachment the client sends for the agent to see. Mirrors the chat-api's
 * `InboundImage` / `InboundFile` wire shape: exactly one of `data_base64` / `url`
 * is set (a base64 `data:` URI may also ride in `url`). */
export interface InboundAttachment {
  mime_type?: string;
  filename?: string;
  url?: string;
  data_base64?: string;
}

export interface ChatMessageBody {
  /** Stable id that scopes durable memory for this speaker (see api.py `_scoped`). */
  user_id: string;
  /** The operator's message (may be empty when attachments are sent). */
  text: string;
  /** Images the agent should see this turn (chat-api `images[]`). */
  images?: InboundAttachment[];
  /** Non-image files the agent should see this turn (chat-api `files[]`). */
  files?: InboundAttachment[];
}

/** Open the chat-api's SSE stream for one session and return the raw upstream
 * Response, so the BFF route can pipe its body straight to the browser without
 * ever handing the bearer token to the client. The reply arrives as `delta`
 * frames (incremental text) terminated by a `done` frame (final text + media).
 * Throws only on a transport error — a non-2xx upstream is returned as-is for
 * the caller to translate. */
export async function openMessageStream(
  sessionId: string,
  body: ChatMessageBody,
): Promise<Response> {
  return fetch(
    `${baseUrl()}/v1/sessions/${encodeURIComponent(sessionId)}/messages/stream`,
    {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    },
  );
}

/** The bot's presented identity (name, description, whether a picture is set),
 * mirroring the chat-api `IdentityOut`. The chat UI renders this as the assistant's
 * face + name. `version` moves on every edit so the avatar cache can be busted. */
export interface BotIdentity {
  display_name: string;
  description: string;
  has_avatar: boolean;
  avatar_mime: string | null;
  version: string;
}

/** Read the bot identity, or null when the chat-api is unreachable — the chat UI
 * just falls back to the default avatar rather than failing the page. */
export async function getIdentity(): Promise<BotIdentity | null> {
  try {
    const res = await fetch(`${baseUrl()}/v1/identity`, {
      headers: authHeaders(),
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as BotIdentity;
  } catch {
    return null;
  }
}

/** Open the bot's profile-picture bytes from the chat-api, returning the raw
 * upstream Response so the BFF can relay it (404 when no picture is set). */
export async function fetchIdentityAvatar(): Promise<Response> {
  return fetch(`${baseUrl()}/v1/identity/avatar`, {
    headers: authHeaders(),
    cache: "no-store",
  });
}

/** Chat-api liveness. Returns its /healthz body, or null when unreachable — the
 * page surfaces an online/offline indicator without failing outright. */
export async function getChatHealth(): Promise<Record<string, unknown> | null> {
  try {
    const res = await fetch(`${baseUrl()}/healthz`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
}
