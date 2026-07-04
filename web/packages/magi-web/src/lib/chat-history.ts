// Per-session transcript persistence for the chat console. assistant-ui's
// LocalRuntime keeps the visible transcript in memory only — switching sessions or
// reloading wipes it. This ThreadHistoryAdapter mirrors the transcript to the SERVER
// (via the BFF at /api/chat/history/<sessionId>), which stores it on disk under the
// OS temp dir — transcripts carry inline image bytes and don't fit localStorage.
//
// The runtime calls `load()` on mount to rehydrate and `append()` as each message
// reaches a terminal status. One adapter instance is bound to one session id.

import {
  ExportedMessageRepository,
  type ExportedMessageRepositoryItem,
  type ThreadHistoryAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";

// Stored form: ThreadMessageLike is the lenient shape assistant-ui can re-import
// (dates/parts/attachments are all normalized by `fromBranchableArray` on load).
type StoredItem = { message: ThreadMessageLike; parentId: string | null };
type StoredThread = { headId: string | null; items: StoredItem[] };

function historyUrl(sessionId: string): string {
  return `/api/chat/history/${encodeURIComponent(sessionId)}`;
}

/** A ThreadHistoryAdapter that mirrors one session's transcript to the server's
 * temp-dir store. Keeps an in-memory copy so each `append` can PUT the whole thread
 * (the server just persists bytes — no read-modify-write races). */
export function createSessionHistoryAdapter(sessionId: string): ThreadHistoryAdapter {
  let thread: StoredThread = { headId: null, items: [] };
  // Serialize writes so overlapping appends apply in order (last issued wins).
  let writeChain: Promise<void> = Promise.resolve();

  function persist(): Promise<void> {
    const snapshot = JSON.stringify(thread);
    writeChain = writeChain
      .then(async () => {
        await fetch(historyUrl(sessionId), {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: snapshot,
        });
      })
      .catch(() => {
        /* offline / write failed — the in-memory transcript still works this session */
      });
    return writeChain;
  }

  return {
    async load() {
      try {
        const res = await fetch(historyUrl(sessionId), { cache: "no-store" });
        if (res.ok) {
          const data = (await res.json()) as Partial<StoredThread>;
          thread = {
            headId: data.headId ?? null,
            items: Array.isArray(data.items) ? data.items : [],
          };
        }
      } catch {
        /* server unreachable → start empty */
      }
      if (thread.items.length === 0) return { messages: [] };
      // createdAt round-trips through JSON as an ISO string; revive it to a Date so
      // it matches ThreadMessageLike before re-import.
      const revived = thread.items.map((item) => ({
        parentId: item.parentId,
        message: {
          ...item.message,
          createdAt: item.message.createdAt
            ? new Date(item.message.createdAt as unknown as string)
            : undefined,
        },
      }));
      const head = thread.headId ?? revived[revived.length - 1]?.message.id ?? null;
      return ExportedMessageRepository.fromBranchableArray(revived, { headId: head });
    },

    async append(item: ExportedMessageRepositoryItem) {
      const stored: StoredItem = {
        parentId: item.parentId,
        message: item.message as ThreadMessageLike,
      };
      // Upsert by id: append fires once per message, but may re-fire on edits/continue.
      const idx = thread.items.findIndex((i) => i.message.id === item.message.id);
      if (idx === -1) thread.items.push(stored);
      else thread.items[idx] = stored;
      thread.headId = item.message.id ?? thread.headId;
      await persist();
    },

    async delete(items: ExportedMessageRepositoryItem[]) {
      const ids = new Set(items.map((i) => i.message.id));
      thread.items = thread.items.filter((i) => !ids.has(i.message.id ?? ""));
      await persist();
    },
  };
}

/** Drop a session's persisted transcript (called when its conversation is deleted).
 * Fire-and-forget — the UI doesn't wait on the delete. */
export function clearSessionHistory(sessionId: string): void {
  void fetch(historyUrl(sessionId), { method: "DELETE" }).catch(() => {});
}
