// Server-only client for the Python admin-api. The bearer token lives here, on
// the server, and is NEVER sent to the browser — this module must only ever be
// imported from server components or route handlers (the BFF). See ADR 0002.

import "server-only";

import type { paths } from "./api-types";

function baseUrl(): string {
  return process.env.ADMIN_API_URL ?? "http://127.0.0.1:8100";
}

function authHeaders(): Record<string, string> {
  const token = process.env.ADMIN_AUTH_TOKEN;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** GET a JSON path on the admin-api with the server-side bearer. Throws on non-2xx. */
export async function adminGet<T>(path: string): Promise<T> {
  const res = await fetch(`${baseUrl()}${path}`, {
    headers: { ...authHeaders() },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`admin-api GET ${path} failed: ${res.status}`);
  }
  return (await res.json()) as T;
}

// Typed convenience helpers, each shaped by the generated OpenAPI types.
type Body<P extends keyof paths> =
  paths[P]["get"]["responses"]["200"]["content"]["application/json"];

export async function listKnowledgeDocuments(): Promise<
  Body<"/admin/v1/knowledge/documents">
> {
  return adminGet("/admin/v1/knowledge/documents");
}

export async function listUsers(): Promise<Body<"/admin/v1/memory/users">> {
  return adminGet("/admin/v1/memory/users");
}

export async function getProfile(
  userId: string,
): Promise<Body<"/admin/v1/memory/users/{user_id}/profile">> {
  return adminGet(`/admin/v1/memory/users/${encodeURIComponent(userId)}/profile`);
}

export async function listSessions(
  userId: string,
): Promise<Body<"/admin/v1/memory/users/{user_id}/sessions">> {
  return adminGet(`/admin/v1/memory/users/${encodeURIComponent(userId)}/sessions`);
}

export async function getSession(
  userId: string,
  sessionId: string,
): Promise<Body<"/admin/v1/memory/users/{user_id}/sessions/{session_id}">> {
  return adminGet(
    `/admin/v1/memory/users/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(
      sessionId,
    )}`,
  );
}

export async function getPersona(): Promise<Body<"/admin/v1/memory/persona">> {
  return adminGet("/admin/v1/memory/persona");
}
