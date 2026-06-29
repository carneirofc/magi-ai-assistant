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

// Typed convenience: the document list, shaped by the generated OpenAPI types.
type DocumentList =
  paths["/admin/v1/knowledge/documents"]["get"]["responses"]["200"]["content"]["application/json"];

export async function listKnowledgeDocuments(): Promise<DocumentList> {
  return adminGet<DocumentList>("/admin/v1/knowledge/documents");
}
