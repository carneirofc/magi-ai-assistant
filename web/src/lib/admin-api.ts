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


// Typed convenience helpers, each shaped by the generated OpenAPI types. A
// conditional `infer` so it resolves only for paths that actually expose a GET
// with a JSON 200 (generated types mark missing methods as `never`).
type JsonOf<G> = G extends { responses: { 200: { content: { "application/json": infer R } } } }
  ? R
  : never;
type Body<P extends keyof paths> = paths[P] extends { get: infer G } ? JsonOf<G> : never;

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

/** Liveness probe. Returns the admin-api's healthz body, or null when unreachable
 * — callers surface a backend-status indicator without failing the whole page. */
export async function getHealth(): Promise<Record<string, unknown> | null> {
  try {
    const res = await adminRequest("/healthz");
    if (!res.ok) return null;
    return (await res.json()) as Record<string, unknown>;
  } catch {
    return null;
  }
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

// --- ingest -----------------------------------------------------------------
export function ingestDocument(doc: {
  title: string;
  text: string;
  subject?: string;
  tags?: string[];
  doc_id?: string;
}): Promise<Response> {
  return adminRequest("/admin/v1/knowledge/documents", {
    method: "POST",
    body: JSON.stringify(doc),
  });
}

// --- subjects ---------------------------------------------------------------
export async function listSubjects(): Promise<Body<"/admin/v1/knowledge/subjects">> {
  return adminGet("/admin/v1/knowledge/subjects");
}

export async function listTags(): Promise<Body<"/admin/v1/knowledge/tags">> {
  return adminGet("/admin/v1/knowledge/tags");
}

export function createSubject(name: string, description = ""): Promise<Response> {
  return adminRequest("/admin/v1/knowledge/subjects", {
    method: "POST",
    body: JSON.stringify({ name, description }),
  });
}

export function editSubject(
  id: string,
  patch: { name?: string; description?: string },
): Promise<Response> {
  return adminRequest(`/admin/v1/knowledge/subjects/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function deleteSubject(id: string): Promise<Response> {
  return adminRequest(`/admin/v1/knowledge/subjects/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

// --- document subject / tags ------------------------------------------------
export function setDocumentSubject(docId: string, subject: string): Promise<Response> {
  return adminRequest(`/admin/v1/knowledge/documents/${encodeDocId(docId)}/subject`, {
    method: "PUT",
    body: JSON.stringify({ subject }),
  });
}

export function editDocumentTags(
  docId: string,
  change: { add?: string[]; remove?: string[] },
): Promise<Response> {
  return adminRequest(`/admin/v1/knowledge/documents/${encodeDocId(docId)}/tags`, {
    method: "PATCH",
    body: JSON.stringify(change),
  });
}

// --- memory facts -----------------------------------------------------------
function factsPath(userId: string, factId?: string): string {
  const base = `/admin/v1/memory/users/${encodeURIComponent(userId)}/facts`;
  return factId ? `${base}/${encodeURIComponent(factId)}` : base;
}

export function addFact(userId: string, text: string, expectedVersion?: string): Promise<Response> {
  return adminRequest(factsPath(userId), {
    method: "POST",
    body: JSON.stringify({ text, expected_version: expectedVersion }),
  });
}

export function updateFact(
  userId: string,
  factId: string,
  text: string,
  expectedVersion?: string,
): Promise<Response> {
  return adminRequest(factsPath(userId, factId), {
    method: "PATCH",
    body: JSON.stringify({ text, expected_version: expectedVersion }),
  });
}

export function deleteFact(
  userId: string,
  factId: string,
  expectedVersion?: string,
): Promise<Response> {
  const q = expectedVersion ? `?expected_version=${encodeURIComponent(expectedVersion)}` : "";
  return adminRequest(`${factsPath(userId, factId)}${q}`, { method: "DELETE" });
}

// --- raw memory files -------------------------------------------------------
function rawFileQuery(userId?: string, sessionId?: string): string {
  const p = new URLSearchParams();
  if (userId) p.set("user_id", userId);
  if (sessionId) p.set("session_id", sessionId);
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function getRawFile(
  kind: string,
  opts: { userId?: string; sessionId?: string } = {},
): Promise<Body<"/admin/v1/memory/files/{kind}">> {
  return adminGet(
    `/admin/v1/memory/files/${encodeURIComponent(kind)}${rawFileQuery(opts.userId, opts.sessionId)}`,
  );
}

export function putRawFile(
  kind: string,
  content: string,
  opts: { userId?: string; sessionId?: string; expectedVersion?: string } = {},
): Promise<Response> {
  return adminRequest(
    `/admin/v1/memory/files/${encodeURIComponent(kind)}${rawFileQuery(opts.userId, opts.sessionId)}`,
    { method: "PUT", body: JSON.stringify({ content, expected_version: opts.expectedVersion }) },
  );
}
