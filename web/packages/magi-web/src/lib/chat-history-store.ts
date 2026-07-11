// Server-only disk store for chat transcripts. Transcripts (which can carry inline
// image bytes) are too big for the browser's localStorage quota, so they live on the
// server's disk instead — one JSON file per session.
//
// Where: `CHAT_HISTORY_DIR` when set (a durable path — history and transcript
// search then survive reboots; the companion deployments want this), else the
// OS temp dir (deliberately ephemeral: a scratch playground store).
//
// Import this ONLY from route handlers / server components — it touches the filesystem.

import "server-only";

import { promises as fs } from "fs";
import os from "os";
import path from "path";

const DIR = process.env.CHAT_HISTORY_DIR
  ? path.resolve(process.env.CHAT_HISTORY_DIR)
  : path.join(os.tmpdir(), "magi-chat-history");

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

// --- search -------------------------------------------------------------------
export type TranscriptHit = {
  sessionId: string;
  /** The matched text with a little context either side. */
  snippet: string;
  /** Who said the matched line. */
  role: "user" | "assistant";
  /** File mtime (ms) — a good-enough "when" for ordering results. */
  ts: number;
};

type SearchableMessage = { role?: string; content?: unknown };
type SearchableItem = { message?: SearchableMessage };

function textParts(content: unknown): string[] {
  if (!Array.isArray(content)) return [];
  const out: string[] = [];
  for (const part of content) {
    if (part && typeof part === "object") {
      const p = part as { type?: string; text?: string };
      if (p.type === "text" && typeof p.text === "string" && p.text) out.push(p.text);
    }
  }
  return out;
}

function snippetAround(text: string, index: number, needle: string): string {
  const radius = 60;
  const start = Math.max(0, index - radius);
  const end = Math.min(text.length, index + needle.length + radius);
  return `${start > 0 ? "…" : ""}${text.slice(start, end).replace(/\s+/g, " ").trim()}${end < text.length ? "…" : ""}`;
}

/** Case-insensitive substring search across every stored transcript. Returns at
 * most one hit per session (its first match), newest sessions first, capped at
 * `limit`. Deliberately simple — the store is a per-operator scratch archive,
 * dozens of files, not a corpus. */
export async function searchThreads(query: string, limit = 20): Promise<TranscriptHit[]> {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  let names: string[];
  try {
    names = (await fs.readdir(DIR)).filter((n) => n.endsWith(".json"));
  } catch {
    return []; // store not created yet
  }

  const hits: TranscriptHit[] = [];
  for (const name of names) {
    const file = path.join(DIR, name);
    let raw: string;
    let mtime = 0;
    try {
      raw = await fs.readFile(file, "utf8");
      mtime = (await fs.stat(file)).mtimeMs;
    } catch {
      continue;
    }
    let thread: { items?: SearchableItem[] };
    try {
      thread = JSON.parse(raw) as { items?: SearchableItem[] };
    } catch {
      continue;
    }
    for (const item of thread.items ?? []) {
      const role = item.message?.role === "user" ? "user" : "assistant";
      let found = false;
      for (const text of textParts(item.message?.content)) {
        const index = text.toLowerCase().indexOf(needle);
        if (index === -1) continue;
        hits.push({
          sessionId: name.slice(0, -".json".length),
          snippet: snippetAround(text, index, needle),
          role,
          ts: mtime,
        });
        found = true;
        break;
      }
      if (found) break; // one hit per session
    }
  }
  hits.sort((a, b) => b.ts - a.ts);
  return hits.slice(0, limit);
}
