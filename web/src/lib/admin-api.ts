// Server-only client for the Python admin-api. The bearer token lives here, on
// the server, and is NEVER sent to the browser — this module must only ever be
// imported from server components or route handlers (the BFF). See ADR 0002.

import "server-only";

import type { paths } from "./api-types";
import { encodeDocId } from "./encode";

export { encodeDocId };

function baseUrl(): string {
  return process.env.ADMIN_API_URL ?? "http://127.0.0.1:8100";
}

function authHeaders(): Record<string, string> {
  const token = process.env.ADMIN_AUTH_TOKEN;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Low-level request to the admin-api with the server-side bearer. Returns the raw
 * Response so callers (BFF routes) can relay status codes. */
export async function adminRequest(path: string, init?: RequestInit): Promise<Response> {
  const headers: Record<string, string> = { ...authHeaders() };
  if (init?.body) headers["Content-Type"] = "application/json";
  return fetch(`${baseUrl()}${path}`, { ...init, headers, cache: "no-store" });
}

/** GET a JSON path on the admin-api with the server-side bearer. Throws on non-2xx. */
export async function adminGet<T>(path: string): Promise<T> {
  const res = await adminRequest(path);
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

export async function getKnowledgeDocument(
  docId: string,
): Promise<Body<"/admin/v1/knowledge/documents/{doc_id}">> {
  return adminGet(`/admin/v1/knowledge/documents/${encodeDocId(docId)}`);
}

/** Rename a document's title (proxied by the BFF). Returns the admin-api Response. */
export function renameDocument(docId: string, title: string): Promise<Response> {
  return adminRequest(`/admin/v1/knowledge/documents/${encodeDocId(docId)}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

/** Delete a document (proxied by the BFF). Returns the admin-api Response. */
export function deleteDocument(docId: string): Promise<Response> {
  return adminRequest(`/admin/v1/knowledge/documents/${encodeDocId(docId)}`, {
    method: "DELETE",
  });
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
