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

// --- bot identity -----------------------------------------------------------
// Hand-typed (like introspection-types) rather than derived from the generated
// OpenAPI `paths`, so these helpers don't require regenerating api-types.ts.
export interface AdminIdentity {
  display_name: string;
  description: string;
  has_avatar: boolean;
  avatar_mime: string | null;
  avatar_filename: string | null;
  version: string;
}

export async function getIdentity(): Promise<AdminIdentity> {
  return adminGet<AdminIdentity>("/admin/v1/identity");
}

/** Set the bot's name + description (proxied by the BFF). Relays 409 on a stale
 * version. Returns the admin-api Response. */
export function updateIdentity(body: {
  display_name: string;
  description: string;
  expectedVersion?: string;
}): Promise<Response> {
  return adminRequest("/admin/v1/identity", {
    method: "PUT",
    body: JSON.stringify({
      display_name: body.display_name,
      description: body.description,
      expected_version: body.expectedVersion,
    }),
  });
}

/** Upload a new profile picture (base64 bytes + mime). Returns the admin-api Response. */
export function putIdentityAvatar(body: {
  dataBase64: string;
  mimeType: string;
  filename?: string;
  expectedVersion?: string;
}): Promise<Response> {
  return adminRequest("/admin/v1/identity/avatar", {
    method: "PUT",
    body: JSON.stringify({
      data_base64: body.dataBase64,
      mime_type: body.mimeType,
      filename: body.filename,
      expected_version: body.expectedVersion,
    }),
  });
}

/** Clear the profile picture. Returns the admin-api Response. */
export function deleteIdentityAvatar(expectedVersion?: string): Promise<Response> {
  const q = expectedVersion ? `?expected_version=${encodeURIComponent(expectedVersion)}` : "";
  return adminRequest(`/admin/v1/identity/avatar${q}`, { method: "DELETE" });
}

/** Open the current profile-picture bytes from the admin-api (404 when none) — so
 * the settings page can preview without depending on the chat-api being up. */
export function fetchIdentityAvatar(): Promise<Response> {
  return adminRequest("/admin/v1/identity/avatar");
}

// --- operator settings: memory location + git-versioning --------------------
export interface AdminMemorySettings {
  memory_dir: string;
  git_enabled: boolean;
  git_author_name: string;
  git_author_email: string;
  /** Where the running process actually reads/writes memory right now. */
  active_memory_dir: string;
  /** True when the saved settings differ from the running process (restart to apply). */
  restart_required: boolean;
  version: string;
}

export async function getMemorySettings(): Promise<AdminMemorySettings> {
  return adminGet<AdminMemorySettings>("/admin/v1/settings/memory");
}

/** Save the memory location + git-versioning (applied on the next restart). Relays
 * 409 on a stale version and 503 when the settings store isn't wired. */
export function updateMemorySettings(body: {
  memory_dir: string;
  git_enabled: boolean;
  git_author_name: string;
  git_author_email: string;
  expectedVersion?: string;
}): Promise<Response> {
  return adminRequest("/admin/v1/settings/memory", {
    method: "PUT",
    body: JSON.stringify({
      memory_dir: body.memory_dir,
      git_enabled: body.git_enabled,
      git_author_name: body.git_author_name,
      git_author_email: body.git_author_email,
      expected_version: body.expectedVersion,
    }),
  });
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

// --- operator-triggered memory passes ---------------------------------------
// Hand-typed (like the identity helpers) rather than derived from the generated
// OpenAPI `paths`, so adding these doesn't require regenerating api-types.ts.
export type MemoryTriggerAction = "summarize" | "curate" | "flush";

export interface MemoryTriggerResult {
  action: string;
  changed: boolean;
  detail: string;
}

/** Run an operator-triggered memory pass on one session (proxied by the BFF).
 * Relays the admin-api status verbatim — notably 503 when the deployment has no
 * model wired for the model-backed passes (summarize / curate). */
export function triggerSessionMemory(
  userId: string,
  sessionId: string,
  action: MemoryTriggerAction,
): Promise<Response> {
  return adminRequest(
    `/admin/v1/memory/users/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(
      sessionId,
    )}/${action}`,
    { method: "POST" },
  );
}
