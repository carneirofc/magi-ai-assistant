// Server-only disk store for chat transcripts. Transcripts (which can carry inline
// image bytes) are too big for the browser's localStorage quota, so they live on the
// server's disk instead — one JSON file per session under the OS temp dir. This is
// deliberately ephemeral (temp dir): a scratch playground store, not durable history.
//
// Import this ONLY from route handlers / server components — it touches the filesystem.

import "server-only";

import { promises as fs } from "fs";
import os from "os";
import path from "path";

const DIR = path.join(os.tmpdir(), "magi-chat-history");

/** A safe filename for a session id, or null if the id looks unsafe. Session ids
 * are `web-<uuid>` / `oai-<hex>` — a conservative charset with no path separators,
 * so a crafted id can't escape `DIR` (defends against path traversal). */
function safeName(sessionId: string): string | null {
  if (!/^[A-Za-z0-9._-]{1,200}$/.test(sessionId) || sessionId.includes("..")) return null;
  return `${sessionId}.json`;
}

/** The stored transcript for a session, or null when none exists / id is unsafe. */
export async function readThread(sessionId: string): Promise<unknown | null> {
  const name = safeName(sessionId);
  if (!name) return null;
  try {
    return JSON.parse(await fs.readFile(path.join(DIR, name), "utf8"));
  } catch {
    return null; // no file yet, or unreadable — treated as empty
  }
}

/** Persist a session's transcript. Returns false only for an unsafe session id. */
export async function writeThread(sessionId: string, data: unknown): Promise<boolean> {
  const name = safeName(sessionId);
  if (!name) return false;
  await fs.mkdir(DIR, { recursive: true });
  await fs.writeFile(path.join(DIR, name), JSON.stringify(data), "utf8");
  return true;
}

/** Delete a session's transcript file (no-op if it's already gone). */
export async function deleteThread(sessionId: string): Promise<void> {
  const name = safeName(sessionId);
  if (!name) return;
  try {
    await fs.unlink(path.join(DIR, name));
  } catch {
    /* already absent */
  }
}
