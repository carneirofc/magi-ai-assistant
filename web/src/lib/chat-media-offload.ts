// Moves inline media OUT of a transcript before it's persisted: any `data:` image
// or file payload is written to the blob store (blob-store.ts) and replaced by a
// reference URL (/api/chat/blobs/<id>). So the transcript file on disk holds only
// small references, and the bytes live in local-file or S3 storage.
//
// Non-inline sources (http(s) URLs, already-offloaded blob refs) are left untouched.
// Server-only.

import "server-only";

import { getBlobStore } from "./blob-store";

const BLOB_URL_PREFIX = "/api/chat/blobs/";

/** Decode a `data:<mime>[;base64],<payload>` URL to bytes + mime, or null. */
function parseDataUrl(url: string): { bytes: Buffer; mime: string } | null {
  if (!url.startsWith("data:")) return null;
  const comma = url.indexOf(",");
  if (comma === -1) return null;
  const header = url.slice("data:".length, comma);
  const mime = header.split(";")[0] || "application/octet-stream";
  const payload = url.slice(comma + 1);
  try {
    const bytes = header.includes(";base64")
      ? Buffer.from(payload, "base64")
      : Buffer.from(decodeURIComponent(payload), "utf8");
    return { bytes, mime };
  } catch {
    return null;
  }
}

/** If `url` is an inline data: URL, store its bytes and return the blob ref URL;
 * otherwise undefined (caller keeps the original). */
async function offloadUrl(url: unknown, preferMime?: string): Promise<string | undefined> {
  if (typeof url !== "string") return undefined;
  const parsed = parseDataUrl(url);
  if (!parsed) return undefined;
  const id = await getBlobStore().put(parsed.bytes, preferMime || parsed.mime);
  return `${BLOB_URL_PREFIX}${id}`;
}

/** Replace inline media in a parts array (image `image`, file `data`) in place. */
async function offloadParts(parts: unknown): Promise<void> {
  if (!Array.isArray(parts)) return;
  for (const part of parts) {
    if (!part || typeof part !== "object") continue;
    const p = part as Record<string, unknown>;
    if (p.type === "image") {
      const ref = await offloadUrl(p.image);
      if (ref) p.image = ref;
    } else if (p.type === "file") {
      const mime = typeof p.mimeType === "string" ? p.mimeType : undefined;
      const ref = await offloadUrl(p.data, mime);
      if (ref) p.data = ref;
    }
  }
}

/** Wire shape of one reply media item — the chat-api `MediaItem` (see
 * channels/api.py): inline bytes ride in `data_base64`, by-reference media in `url`. */
interface WireMediaItem {
  kind?: string;
  mime_type?: string;
  data_base64?: string;
  url?: string;
}

/** Offload the inline bytes of a `done` frame's reply media to the blob store,
 * rewriting each inline item to a cacheable blob-ref URL (/api/chat/blobs/<id>)
 * and dropping its base64. Items that already carry a `url` (by-reference media)
 * are left untouched. Mutates `media` in place and returns it. Best-effort: an
 * item that fails to store stays inline. Runs in the live stream (route.ts) so
 * the browser fetches reply images from the cacheable blob endpoint instead of
 * holding a one-shot data: URI. */
export async function offloadReplyMedia(media: unknown): Promise<unknown> {
  if (!Array.isArray(media)) return media;
  const store = getBlobStore();
  for (const item of media) {
    if (!item || typeof item !== "object") continue;
    const m = item as WireMediaItem;
    if (m.url || typeof m.data_base64 !== "string" || !m.data_base64) continue;
    const mime =
      m.mime_type || (m.kind === "image" ? "image/png" : "application/octet-stream");
    try {
      const id = await store.put(Buffer.from(m.data_base64, "base64"), mime);
      m.url = `${BLOB_URL_PREFIX}${id}`;
      delete m.data_base64;
    } catch {
      /* leave this item inline on failure */
    }
  }
  return media;
}

/** Walk a stored transcript ({ items: [{ message }] }) and offload every inline
 * image/file to the blob store, mutating the object in place. Returns it for
 * convenience. Best-effort: a blob-store failure leaves that part inline. */
export async function offloadTranscriptMedia(thread: unknown): Promise<unknown> {
  if (!thread || typeof thread !== "object") return thread;
  const items = (thread as { items?: unknown }).items;
  if (!Array.isArray(items)) return thread;
  for (const item of items) {
    if (!item || typeof item !== "object") continue;
    const message = (item as Record<string, unknown>).message;
    if (!message || typeof message !== "object") continue;
    const m = message as Record<string, unknown>;
    await offloadParts(m.content);
    if (Array.isArray(m.attachments)) {
      for (const att of m.attachments) {
        if (att && typeof att === "object") {
          await offloadParts((att as Record<string, unknown>).content);
        }
      }
    }
  }
  return thread;
}
