// assistant-ui ChatModelAdapter that drives the chat console off our BFF SSE route
// (/api/chat → chat-api /v1/sessions/{id}/messages/stream). The assistant keeps its
// own per-(user, session) context server-side, so each run transmits only the
// newest user turn (its text + attachments); the local runtime keeps the visible
// transcript.
//
// The stream is rich: an early `meta` frame carries the turn's mood (the
// engine's pre-reply pass — before the first content token, so the UI can react
// as the answer starts), `delta` frames grow the assistant text, `reasoning`
// frames grow a collapsible thinking block, `tool_call`/`tool_result` frames
// build live tool cards, and the terminal `done` frame carries the
// authoritative final text plus any produced media (images inline, other files
// as links) and echoes the mood.

import type {
  ChatModelAdapter,
  FileMessagePart,
  ImageMessagePart,
  ThreadAssistantMessagePart,
  ThreadMessage,
  ToolCallMessagePart,
} from "@assistant-ui/react";

import type { InboundAttachment } from "./chat-api";
import type { ChatLifecycle } from "./chat-mood";

/** Where the console draws its session/user scoping from at send time. Read
 * lazily so edits to the user id (or a "New chat") take effect on the next run
 * without rebuilding the runtime. */
export type ChatConfig = { sessionId: string; userId: string };

/** Token accounting for one turn, mirrored off the chat-api `done` frame's
 * `usage` object (see channels/api.py `Usage`). Best-effort — some backends
 * under-report — so the console renders it as observability, not a hard budget. */
export type ChatUsage = {
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  cachedTokens: number;
  reasoningTokens: number;
  contextWindow: number | null;
};

/** Lift the `done` frame's `usage` payload into a `ChatUsage`; null when the run
 * reported no metrics (the field is absent or not an object). */
function parseUsage(raw: unknown): ChatUsage | null {
  if (raw === null || typeof raw !== "object") return null;
  const u = raw as Record<string, unknown>;
  const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);
  const window = u.context_window;
  return {
    inputTokens: num(u.input_tokens),
    outputTokens: num(u.output_tokens),
    totalTokens: num(u.total_tokens),
    cachedTokens: num(u.cached_tokens),
    reasoningTokens: num(u.reasoning_tokens),
    contextWindow: typeof window === "number" && Number.isFinite(window) ? window : null,
  };
}

export type SseFrame = { event: string; data: Record<string, unknown> };

/** Parse one `event:`/`data:` SSE frame; null when it carries no JSON payload.
 * Exported for the other stream consumers (the greeting flow). */
export function parseFrame(frame: string): SseFrame | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) as Record<string, unknown> };
  } catch {
    return null;
  }
}

/** The most recent user message — the only turn we forward, since the assistant
 * reconstructs the rest of the conversation from its session memory. */
function latestUserMessage(messages: readonly ThreadMessage[]): ThreadMessage | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") return messages[i];
  }
  return null;
}

/** The plain text of a user message (its text parts joined). */
function userText(message: ThreadMessage | null): string {
  if (!message) return "";
  return message.content
    .filter((part): part is { type: "text"; text: string } => part.type === "text")
    .map((part) => part.text)
    .join("");
}

/** The text the user quoted from a prior reply, if any. assistant-ui's quote
 * feature stashes the selected span on the outgoing message as
 * `metadata.custom.quote` (a `QuoteInfo`); we read it directly since the adapter
 * runs outside React (no `useMessageQuote`). */
function quotedText(message: ThreadMessage | null): string {
  const quote = message?.metadata.custom["quote"];
  if (quote && typeof quote === "object") {
    const text = (quote as { text?: unknown }).text;
    if (typeof text === "string") return text;
  }
  return "";
}

/** Fold a quoted span into the outgoing text as a Markdown blockquote, so the
 * assistant sees what the user is citing ahead of their message. */
function withQuote(text: string, quote: string): string {
  if (!quote) return text;
  const block = quote
    .split("\n")
    .map((line) => `> ${line}`)
    .join("\n");
  return text ? `${block}\n\n${text}` : block;
}

/** A `data:` URI is inline bytes the backend decodes; an http(s) URL is passed
 * by reference. Split a source string into the right `InboundAttachment` fields. */
function attachmentFromSource(
  src: string,
  mimeType?: string,
  filename?: string,
): InboundAttachment {
  const base: InboundAttachment = {};
  if (mimeType) base.mime_type = mimeType;
  if (filename) base.filename = filename;
  // A data: URI (or a raw-looking base64 blob) travels inline; anything else is a URL.
  if (src.startsWith("data:") || src.startsWith("http://") || src.startsWith("https://")) {
    base.url = src;
  } else {
    base.data_base64 = src;
  }
  return base;
}

/** The image + file attachments of the latest user turn, in the chat-api wire
 * shape. assistant-ui keeps sent attachments under `message.attachments[].content`
 * (NOT lifted into `message.content`), each a set of `image` / `file` parts our
 * attachment adapters produced (see chat-attachments.ts). */
function outboundAttachments(message: ThreadMessage | null): {
  images: InboundAttachment[];
  files: InboundAttachment[];
} {
  const images: InboundAttachment[] = [];
  const files: InboundAttachment[] = [];
  if (!message || message.role !== "user") return { images, files };
  for (const attachment of message.attachments ?? []) {
    for (const part of attachment.content ?? []) {
      if (part.type === "image" && typeof part.image === "string" && part.image) {
        images.push(attachmentFromSource(part.image, undefined, part.filename ?? attachment.name));
      } else if (part.type === "file" && typeof part.data === "string" && part.data) {
        files.push(
          attachmentFromSource(part.data, part.mimeType, part.filename ?? attachment.name),
        );
      }
    }
  }
  return { images, files };
}

/** Turn the chat-api's MediaItem[] (from the `done` frame) into assistant-ui
 * parts: images render inline, everything else as a downloadable file. */
function replyMediaParts(media: unknown): (ImageMessagePart | FileMessagePart)[] {
  if (!Array.isArray(media)) return [];
  const parts: (ImageMessagePart | FileMessagePart)[] = [];
  for (const raw of media) {
    if (raw === null || typeof raw !== "object") continue;
    const item = raw as {
      kind?: string;
      mime_type?: string;
      url?: string;
      data_base64?: string;
      filename?: string;
    };
    const mime = item.mime_type ?? (item.kind === "image" ? "image/png" : "application/octet-stream");
    const src = item.url
      ? item.url
      : item.data_base64
        ? `data:${mime};base64,${item.data_base64}`
        : null;
    if (!src) continue;
    if (item.kind === "image") {
      parts.push({ type: "image", image: src, ...(item.filename ? { filename: item.filename } : {}) });
    } else {
      parts.push({
        type: "file",
        data: src,
        mimeType: mime,
        ...(item.filename ? { filename: item.filename } : {}),
      });
    }
  }
  return parts;
}

/** Accumulates the streamed assistant turn (thinking, tool calls, text) and
 * renders it to an ordered assistant-ui part array on demand. */
class StreamAssembly {
  private reasoning = "";
  private answer = "";
  private toolOrder: string[] = [];
  private tools = new Map<string, { name: string; args: object; result?: string; isError?: boolean }>();
  private synthetic = 0;
  private mediaParts: (ImageMessagePart | FileMessagePart)[] = [];

  addReasoning(text: string): void {
    this.reasoning += text;
  }

  addDelta(text: string): void {
    this.answer += text;
  }

  setAnswer(text: string): void {
    this.answer = text;
  }

  setMedia(media: unknown): void {
    this.mediaParts = replyMediaParts(media);
  }

  startTool(id: string, name: string, args: object): void {
    const key = id || `#${++this.synthetic}`;
    this.toolOrder.push(key);
    this.tools.set(key, { name, args });
  }

  finishTool(id: string, result: string, isError: boolean): void {
    // Match by id; fall back to the most recent still-open call (covers backends
    // that don't echo a tool_call_id on the result).
    let key = id && this.tools.has(id) ? id : "";
    if (!key) {
      for (let i = this.toolOrder.length - 1; i >= 0; i--) {
        const candidate = this.tools.get(this.toolOrder[i]);
        if (candidate && candidate.result === undefined) {
          key = this.toolOrder[i];
          break;
        }
      }
    }
    const entry = key ? this.tools.get(key) : undefined;
    if (entry) {
      entry.result = result;
      entry.isError = isError;
    }
  }

  /** The current assistant content: thinking, then tool cards, then the answer
   * text, then any delivered media. */
  parts(): ThreadAssistantMessagePart[] {
    const out: ThreadAssistantMessagePart[] = [];
    if (this.reasoning) out.push({ type: "reasoning", text: this.reasoning });
    for (const key of this.toolOrder) {
      const t = this.tools.get(key);
      if (!t) continue;
      const part: ToolCallMessagePart = {
        type: "tool-call",
        toolCallId: key,
        toolName: t.name,
        args: t.args as ToolCallMessagePart["args"],
        argsText: safeJson(t.args),
        ...(t.result !== undefined ? { result: t.result, isError: t.isError ?? false } : {}),
      };
      out.push(part);
    }
    if (this.answer || out.length === 0) out.push({ type: "text", text: this.answer });
    out.push(...this.mediaParts);
    return out;
  }
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? {});
  } catch {
    return "{}";
  }
}

/** Build an adapter bound to a live `getConfig`, so the current session/user id
 * are resolved at the moment of each run. `onSend`, when given, is called with the
 * outgoing user text on each run (the console uses it to title/reorder sessions).
 * `onMood` fires with the turn's mood as the `meta` frame lands (before the first
 * delta) and again off the `done` frame; `onLifecycle` tracks the turn's phase
 * (thinking → streaming ⇄ tool → idle, or error) — see chat-mood.tsx. */
export function createChatModelAdapter(
  getConfig: () => ChatConfig,
  onSend?: (text: string) => void,
  onUsage?: (usage: ChatUsage) => void,
  onMood?: (mood: string) => void,
  onLifecycle?: (lifecycle: ChatLifecycle) => void,
): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const message = latestUserMessage(messages);
      const text = withQuote(userText(message), quotedText(message));
      const { images, files } = outboundAttachments(message);
      const { sessionId, userId } = getConfig();
      onSend?.(text);
      onLifecycle?.("thinking");

      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sessionId, userId, text, images, files }),
          signal: abortSignal,
        });
        if (!res.ok || !res.body) {
          let detail = `chat request failed (${res.status})`;
          try {
            const body = (await res.json()) as { error?: string };
            if (body.error) detail = body.error;
          } catch {
            /* keep the status-code fallback */
          }
          throw new Error(detail);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        const asm = new StreamAssembly();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let sep = buffer.indexOf("\n\n");
          while (sep !== -1) {
            const frame = parseFrame(buffer.slice(0, sep));
            buffer = buffer.slice(sep + 2);
            sep = buffer.indexOf("\n\n");
            if (!frame) continue;

            if (frame.event === "delta") {
              if (typeof frame.data.text === "string") asm.addDelta(frame.data.text);
              onLifecycle?.("streaming");
            } else if (frame.event === "meta") {
              // The pre-reply mood pass; no transcript content of its own.
              if (typeof frame.data.mood === "string" && frame.data.mood) {
                onMood?.(frame.data.mood);
              }
              continue;
            } else if (frame.event === "reasoning") {
              if (typeof frame.data.text === "string") asm.addReasoning(frame.data.text);
            } else if (frame.event === "tool_call") {
              const id = typeof frame.data.id === "string" ? frame.data.id : "";
              const name = typeof frame.data.name === "string" ? frame.data.name : "tool";
              const args =
                frame.data.args && typeof frame.data.args === "object"
                  ? (frame.data.args as object)
                  : {};
              asm.startTool(id, name, args);
              onLifecycle?.("tool");
            } else if (frame.event === "tool_result") {
              const id = typeof frame.data.id === "string" ? frame.data.id : "";
              const result = typeof frame.data.result === "string" ? frame.data.result : "";
              asm.finishTool(id, result, frame.data.is_error === true);
            } else if (frame.event === "done") {
              const finalText = frame.data.text;
              if (typeof finalText === "string" && finalText.length > 0) asm.setAnswer(finalText);
              asm.setMedia(frame.data.media);
              if (frame.data.is_error === true && !finalText) {
                asm.setAnswer("The assistant reported an error.");
              }
              // The done frame echoes the mood (authoritative; also covers a
              // server that skipped the early meta frame).
              if (typeof frame.data.mood === "string" && frame.data.mood) {
                onMood?.(frame.data.mood);
              }
              const usage = parseUsage(frame.data.usage);
              if (usage) onUsage?.(usage);
            } else {
              continue;
            }
            yield { content: asm.parts() };
          }
        }

        yield { content: asm.parts() };
        onLifecycle?.("idle");
      } catch (err) {
        onLifecycle?.("error");
        throw err;
      }
    },
  };
}
