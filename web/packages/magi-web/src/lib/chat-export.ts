// Export one conversation's transcript from the server-side transcript store
// (the same blob the history adapter persists — see chat-history.ts) as a
// Markdown or JSON download. Markdown walks the ACTIVE branch (headId → parents)
// so an exported chat reads like the conversation on screen; JSON is the raw
// stored repository, branches and all.

type StoredMessage = {
  id?: string;
  role?: string;
  content?: unknown;
  createdAt?: string;
};
type StoredItem = { message?: StoredMessage; parentId?: string | null };
type StoredThread = { headId?: string | null; items?: StoredItem[] };

function messageText(content: unknown): string {
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => {
      if (part === null || typeof part !== "object") return "";
      const p = part as { type?: string; text?: string; image?: string; filename?: string };
      if (p.type === "text" && typeof p.text === "string") return p.text;
      if (p.type === "image") return `![${p.filename ?? "image"}](attached image)`;
      if (p.type === "file") return `[${p.filename ?? "file"}](attached file)`;
      if (p.type === "reasoning") return ""; // thinking stays out of exports
      if (p.type === "tool-call") return "";
      return "";
    })
    .filter(Boolean)
    .join("\n\n");
}

/** The active branch, oldest first: follow headId up the parent chain. */
function activeBranch(thread: StoredThread): StoredMessage[] {
  const items = thread.items ?? [];
  const byId = new Map<string, StoredItem>();
  for (const item of items) {
    if (item.message?.id) byId.set(item.message.id, item);
  }
  const chain: StoredMessage[] = [];
  let cursor = thread.headId ?? null;
  while (cursor) {
    const item = byId.get(cursor);
    if (!item?.message) break;
    chain.unshift(item.message);
    cursor = item.parentId ?? null;
  }
  // A store without a usable head still exports something: document order.
  if (chain.length === 0) {
    return items.map((i) => i.message).filter((m): m is StoredMessage => !!m);
  }
  return chain;
}

function toMarkdown(thread: StoredThread, title: string, assistantName: string): string {
  const lines = [`# ${title}`, ""];
  for (const message of activeBranch(thread)) {
    const text = messageText(message.content);
    if (!text) continue;
    const speaker = message.role === "user" ? "You" : assistantName;
    lines.push(`**${speaker}:**`, "", text, "");
  }
  return lines.join("\n");
}

function download(filename: string, mime: string, body: string): void {
  const url = URL.createObjectURL(new Blob([body], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function safeFilename(title: string): string {
  return title.replace(/[^\w\d-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40) || "chat";
}

/** Download one session's transcript. Resolves false when the session has no
 * stored transcript (nothing to export). */
export async function exportTranscript(
  sessionId: string,
  title: string,
  format: "markdown" | "json",
  assistantName = "Assistant",
): Promise<boolean> {
  let thread: StoredThread | null = null;
  try {
    const res = await fetch(`/api/chat/history/${encodeURIComponent(sessionId)}`, {
      cache: "no-store",
    });
    if (res.ok) thread = (await res.json()) as StoredThread;
  } catch {
    /* treated as no transcript below */
  }
  if (!thread || !Array.isArray(thread.items) || thread.items.length === 0) return false;

  if (format === "json") {
    download(`${safeFilename(title)}.json`, "application/json", JSON.stringify(thread, null, 2));
  } else {
    download(`${safeFilename(title)}.md`, "text/markdown", toMarkdown(thread, title, assistantName));
  }
  return true;
}
