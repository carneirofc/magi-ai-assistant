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
