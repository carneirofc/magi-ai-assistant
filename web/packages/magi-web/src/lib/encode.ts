// Client-safe helpers (no server-only deps), usable from both server and client.

/** Encode a doc_id's segments while keeping the slashes (matches {doc_id:path}). */
export function encodeDocId(docId: string): string {
  return docId.split("/").map(encodeURIComponent).join("/");
}
