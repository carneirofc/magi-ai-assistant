// Server-only client for the Python admin-api. The bearer token lives here, on
// the server, and is NEVER sent to the browser — this module must only ever be
// imported from server components or route handlers (the BFF). See ADR 0002.

import "server-only";

import { z } from "zod";
import { schemas } from "./api-schemas";
import { encodeDocId } from "./encode";
import { getConfigValue } from "./runtime-config";
import { fetchJson } from "./utils";

export { encodeDocId };

// URL + bearer resolve through the runtime-config store (file override → env →
// default), so an operator can repoint the admin-api from the Settings page
// without a restart. See runtime-config.ts.
function baseUrl(): string {
  return getConfigValue("adminApiUrl");
}

function authHeaders(): Record<string, string> {
  const token = getConfigValue("adminAuthToken");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Low-level request to the admin-api with the server-side bearer. Returns the raw
 * Response so callers (BFF routes) can relay status codes. */
export async function adminRequest(path: string, init?: RequestInit): Promise<Response> {
  const headers: Record<string, string> = { ...authHeaders() };
  if (init?.body) headers["Content-Type"] = "application/json";
  return fetch(`${baseUrl()}${path}`, { ...init, headers, cache: "no-store" });
}

/** GET a JSON path on the admin-api with the server-side bearer, validated against
 * `schema`. Throws on non-2xx or a validation mismatch. */
export async function adminGet<T>(path: string, schema: z.ZodType<T>): Promise<T> {
  const res = await adminRequest(path);
  return fetchJson(res, schema);
}

export async function listKnowledgeDocuments(): Promise<z.infer<typeof schemas.DocumentList>> {
  return adminGet("/admin/v1/knowledge/documents", schemas.DocumentList);
}

export async function getKnowledgeDocument(
  docId: string,
): Promise<z.infer<typeof schemas.DocumentDetailOut>> {
  return adminGet(`/admin/v1/knowledge/documents/${encodeDocId(docId)}`, schemas.DocumentDetailOut);
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

const HealthSchema = z.record(z.string(), z.unknown());

/** Liveness probe. Returns the admin-api's healthz body, or null when unreachable
 * — callers surface a backend-status indicator without failing the whole page. */
export async function getHealth(): Promise<Record<string, unknown> | null> {
  try {
    const res = await adminRequest("/healthz");
    if (!res.ok) return null;
    return await fetchJson(res, HealthSchema);
  } catch {
    return null;
  }
}

export async function listUsers(): Promise<z.infer<typeof schemas.UserList>> {
  return adminGet("/admin/v1/memory/users", schemas.UserList);
}

export async function getProfile(userId: string): Promise<z.infer<typeof schemas.Profile>> {
  return adminGet(`/admin/v1/memory/users/${encodeURIComponent(userId)}/profile`, schemas.Profile);
}

export async function listSessions(userId: string): Promise<z.infer<typeof schemas.SessionList>> {
  return adminGet(
    `/admin/v1/memory/users/${encodeURIComponent(userId)}/sessions`,
    schemas.SessionList,
  );
}

export async function getSession(
  userId: string,
  sessionId: string,
): Promise<z.infer<typeof schemas.SessionDetail>> {
  return adminGet(
    `/admin/v1/memory/users/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(
      sessionId,
    )}`,
    schemas.SessionDetail,
  );
}

export async function getPersona(): Promise<z.infer<typeof schemas.Persona>> {
  return adminGet("/admin/v1/memory/persona", schemas.Persona);
}

// --- bot identity -----------------------------------------------------------
export type AdminExpression = z.infer<typeof schemas.ExpressionOut>;
export type AdminIdentity = z.infer<typeof schemas.IdentityOut>;

export async function getIdentity(): Promise<AdminIdentity> {
  return adminGet("/admin/v1/identity", schemas.IdentityOut);
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

// --- expression pack (mood-keyed portraits; `neutral` = the avatar slot) -----
/** Upload one mood's portrait (same payload as the avatar upload). */
export function putIdentityExpression(
  mood: string,
  body: {
    dataBase64: string;
    mimeType: string;
    filename?: string;
    expectedVersion?: string;
  },
): Promise<Response> {
  return adminRequest(`/admin/v1/identity/expressions/${encodeURIComponent(mood)}`, {
    method: "PUT",
    body: JSON.stringify({
      data_base64: body.dataBase64,
      mime_type: body.mimeType,
      filename: body.filename,
      expected_version: body.expectedVersion,
    }),
  });
}

/** Remove one mood's portrait. */
export function deleteIdentityExpression(
  mood: string,
  expectedVersion?: string,
): Promise<Response> {
  const q = expectedVersion ? `?expected_version=${encodeURIComponent(expectedVersion)}` : "";
  return adminRequest(`/admin/v1/identity/expressions/${encodeURIComponent(mood)}${q}`, {
    method: "DELETE",
  });
}

/** Open one mood's portrait bytes (404 when the pack has no such portrait). */
export function fetchIdentityExpression(mood: string): Promise<Response> {
  return adminRequest(`/admin/v1/identity/expressions/${encodeURIComponent(mood)}`);
}

// --- operator settings: memory location + git-versioning --------------------
export type AdminMemorySettings = z.infer<typeof schemas.MemorySettingsOut>;

export async function getMemorySettings(): Promise<AdminMemorySettings> {
  return adminGet("/admin/v1/settings/memory", schemas.MemorySettingsOut);
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
export async function listSubjects(): Promise<z.infer<typeof schemas.SubjectListOut>> {
  return adminGet("/admin/v1/knowledge/subjects", schemas.SubjectListOut);
}

export async function listTags(): Promise<z.infer<typeof schemas.TagList>> {
  return adminGet("/admin/v1/knowledge/tags", schemas.TagList);
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
): Promise<z.infer<typeof schemas.RawFile>> {
  return adminGet(
    `/admin/v1/memory/files/${encodeURIComponent(kind)}${rawFileQuery(opts.userId, opts.sessionId)}`,
    schemas.RawFile,
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
export type MemoryTriggerAction = "summarize" | "curate" | "flush";
export type MemoryTriggerResult = z.infer<typeof schemas.MemoryTriggerResult>;

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

// --- memory depth: consolidation / recall preview / file history --------------

/** Maintenance curation over a user's whole fact sheet (merge duplicates, drop
 * contradictions). 503 when no model is wired. */
export function consolidateFacts(userId: string): Promise<Response> {
  return adminRequest(`/admin/v1/memory/users/${encodeURIComponent(userId)}/consolidate`, {
    method: "POST",
  });
}

export type RecallPreview = z.infer<typeof schemas.RecallPreview>;

/** Dry-run the context assembly for a query — exactly what each memory section
 * would inject (the retrieval-quality lens for semantic memory). */
export async function getRecallPreview(userId: string, q: string): Promise<RecallPreview> {
  return adminGet(
    `/admin/v1/memory/users/${encodeURIComponent(userId)}/recall-preview?q=${encodeURIComponent(q)}`,
    schemas.RecallPreview,
  );
}

export type FileHistoryEntry = z.infer<typeof schemas.FileHistoryEntry>;

/** The git history of one raw memory file. Empty entries — not an error — when
 * memory versioning (memory_git_enabled) is off. */
export async function getRawFileHistory(
  kind: string,
  opts: { userId?: string; sessionId?: string; limit?: number } = {},
): Promise<z.infer<typeof schemas.FileHistory>> {
  const query = rawFileQuery(opts.userId, opts.sessionId);
  const sep = query ? "&" : "?";
  const limit = opts.limit ? `${sep}limit=${opts.limit}` : "";
  return adminGet(
    `/admin/v1/memory/files/${encodeURIComponent(kind)}/history${query}${limit}`,
    schemas.FileHistory,
  );
}

/** One raw memory file's content at a commit; relays 404 for unknown versions. */
export function getRawFileVersion(
  kind: string,
  sha: string,
  opts: { userId?: string; sessionId?: string } = {},
): Promise<Response> {
  return adminRequest(
    `/admin/v1/memory/files/${encodeURIComponent(kind)}/history/${encodeURIComponent(sha)}${rawFileQuery(opts.userId, opts.sessionId)}`,
  );
}
