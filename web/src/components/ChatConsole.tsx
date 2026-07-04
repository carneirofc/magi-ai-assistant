"use client";

// The chat console: an operator playground for talking to the running brain over
// streaming SSE. Built on assistant-ui's LocalRuntime (state, streaming, auto-
// scroll, cancel come for free) but rendered with unstyled primitives themed via
// @carneirofc/ui tokens so it matches the rest of the dashboard.
//
// Feature-rich by design: the composer takes image + file attachments, the
// transcript renders live *thinking* (reasoning) and *tool activity* (tool cards)
// as they stream, and reply media renders inline (images) or as links (files).
//
// The `user_id` scopes durable memory (chat "as" any user to test their memory).
// Conversations are tracked client-side (chat-sessions.ts): the session rail lists
// them, switching re-keys ChatThread → a fresh runtime with an empty transcript,
// while the brain still remembers that session id server-side. The bearer token
// stays server-side; see api/chat/route.ts.

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  AttachmentPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAttachment,
  useLocalRuntime,
  useThread,
  WebSpeechDictationAdapter,
  type FileMessagePartProps,
  type ImageMessagePartProps,
  type ReasoningMessagePartProps,
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { OutlineButton, TextInput } from "@carneirofc/ui";

import { createChatModelAdapter } from "@/lib/chat-adapter";
import { createChatAttachmentAdapter } from "@/lib/chat-attachments";
import { clearSessionHistory, createSessionHistoryAdapter } from "@/lib/chat-history";
import {
  activeSession,
  createSession,
  loadRegistry,
  removeSession,
  renameSession,
  saveRegistry,
  selectSession,
  touchSession,
  type ChatSession,
  type SessionRegistry,
} from "@/lib/chat-sessions";

const USER_KEY = "magi.chat.userId";
const DEFAULT_USER = "console";

export function ChatConsole() {
  const [userId, setUserId] = useState(DEFAULT_USER);
  // Null until mounted so the first client render matches the server (no crypto /
  // localStorage during SSR → no hydration mismatch).
  const [registry, setRegistry] = useState<SessionRegistry | null>(null);

  useEffect(() => {
    setUserId(localStorage.getItem(USER_KEY) || DEFAULT_USER);
    setRegistry(loadRegistry());
  }, []);

  function changeUser(value: string) {
    setUserId(value);
    localStorage.setItem(USER_KEY, value || DEFAULT_USER);
  }

  // One place to apply a registry mutation: persist + set state.
  function commit(next: SessionRegistry) {
    saveRegistry(next);
    setRegistry(next);
  }

  if (!registry) return null;

  const active = activeSession(registry);
  const sessionId = active?.id ?? "";

  return (
    <div className="flex h-[calc(100dvh-15rem)] min-h-[520px] flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
            Chat as user id
          </span>
          <TextInput
            value={userId}
            onChange={(e) => changeUser(e.target.value)}
            spellCheck={false}
            className="w-56 font-mono text-ui-xs"
            aria-label="User id to chat as"
          />
        </label>
        <span
          className="font-mono text-ui-2xs text-[color:var(--ui-ink-subtle)]"
          title="Conversation id (scopes session memory)"
        >
          {sessionId}
        </span>
      </div>

      <div className="flex min-h-0 flex-1 gap-3">
        <SessionRail
          registry={registry}
          onNew={() => commit(createSession(registry))}
          onSelect={(id) => commit(selectSession(registry, id))}
          onRename={(id, title) => commit(renameSession(registry, id, title))}
          onRemove={(id) => {
            clearSessionHistory(id);
            commit(removeSession(registry, id));
          }}
        />

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-ui bg-[color:var(--ui-bg)]">
          <ChatThread
            key={sessionId}
            sessionId={sessionId}
            userId={userId}
            onUserSend={(text) => commit(touchSession(registry, sessionId, text))}
          />
        </div>
      </div>
    </div>
  );
}

// --- session rail ------------------------------------------------------------
function SessionRail({
  registry,
  onNew,
  onSelect,
  onRename,
  onRemove,
}: {
  registry: SessionRegistry;
  onNew: () => void;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onRemove: (id: string) => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  function startRename(session: ChatSession) {
    setEditingId(session.id);
    setDraft(session.title);
  }
  function commitRename() {
    if (editingId) onRename(editingId, draft);
    setEditingId(null);
  }

  return (
    <aside className="flex w-52 shrink-0 flex-col gap-2 overflow-hidden rounded-xl border border-ui bg-[color:var(--ui-bg-soft)] p-2">
      <OutlineButton variant="accent" controlSize="md" onClick={onNew} className="w-full">
        + New chat
      </OutlineButton>
      <ul className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
        {registry.sessions.map((session) => {
          const isActive = session.id === registry.activeId;
          return (
            <li key={session.id}>
              {editingId === session.id ? (
                <TextInput
                  value={draft}
                  autoFocus
                  onChange={(e) => setDraft(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename();
                    if (e.key === "Escape") setEditingId(null);
                  }}
                  className="w-full text-ui-xs"
                  aria-label="Rename conversation"
                />
              ) : (
                <div
                  className={`group flex items-center gap-1 rounded-lg border px-2 py-1.5 ${
                    isActive
                      ? "border-accent-cyan/40 bg-[color:var(--ui-bg-info)]"
                      : "border-transparent hover:border-ui hover:bg-[color:var(--ui-bg)]"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(session.id)}
                    onDoubleClick={() => startRename(session)}
                    className="min-w-0 flex-1 truncate text-left text-ui-xs text-[color:var(--ui-ink)]"
                    title={`${session.title}\nDouble-click to rename`}
                  >
                    {session.title}
                  </button>
                  <button
                    type="button"
                    onClick={() => startRename(session)}
                    className="shrink-0 text-ui-2xs text-[color:var(--ui-ink-subtle)] opacity-0 hover:text-[color:var(--ui-ink)] group-hover:opacity-100"
                    title="Rename"
                    aria-label="Rename conversation"
                  >
                    ✎
                  </button>
                  <button
                    type="button"
                    onClick={() => onRemove(session.id)}
                    className="shrink-0 text-ui-2xs text-[color:var(--ui-ink-subtle)] opacity-0 hover:text-[color:var(--ui-ink-danger)] group-hover:opacity-100"
                    title="Delete conversation"
                    aria-label="Delete conversation"
                  >
                    ✕
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </aside>
  );
}

// --- thread ------------------------------------------------------------------
function ChatThread({
  sessionId,
  userId,
  onUserSend,
}: {
  sessionId: string;
  userId: string;
  onUserSend: (text: string) => void;
}) {
  // Mirror the current user id + send callback into refs so the adapter (built
  // once per session) reads the latest values without rebuilding the runtime.
  const userIdRef = useRef(userId);
  userIdRef.current = userId;
  const onSendRef = useRef(onUserSend);
  onSendRef.current = onUserSend;

  const adapter = useMemo(
    () =>
      createChatModelAdapter(
        () => ({ sessionId, userId: userIdRef.current }),
        (text) => onSendRef.current(text),
      ),
    [sessionId],
  );
  const attachments = useMemo(() => createChatAttachmentAdapter(), []);
  // Voice → text via the browser's Web Speech API (no server round-trip). In a
  // browser without SpeechRecognition the mic button simply stays disabled.
  const dictation = useMemo(() => new WebSpeechDictationAdapter(), []);
  // Persist this session's visible transcript so switching to it (or reloading)
  // restores the conversation, not an empty thread. Bound to this session id.
  const history = useMemo(() => createSessionHistoryAdapter(sessionId), [sessionId]);
  const runtime = useLocalRuntime(adapter, {
    adapters: { attachments, dictation, history },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
        {/* Drop images/files anywhere over the conversation to attach them. */}
        <ComposerPrimitive.AttachmentDropzone className="group relative flex min-h-0 flex-1 flex-col rounded-lg outline-2 -outline-offset-2 outline-[color:var(--ui-border-active)] data-[dragging=true]:outline-dashed">
          <ThreadPrimitive.Viewport className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
            <ThreadPrimitive.Empty>
              <div className="m-auto flex max-w-sm flex-col items-center gap-1 text-center">
                <p className="text-ui-sm font-medium text-[color:var(--ui-ink-muted)]">
                  Talk to the running brain
                </p>
                <p className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
                  Drag in images or files, dictate with the mic, and watch it think and
                  call tools. Messages route through the live team and read/write memory
                  scoped to the user id above.
                </p>
              </div>
            </ThreadPrimitive.Empty>

            <ThreadPrimitive.Messages>
              {({ message }) =>
                message.role === "user" ? <UserMessage /> : <AssistantMessage />
              }
            </ThreadPrimitive.Messages>
          </ThreadPrimitive.Viewport>

          <div className="pointer-events-none absolute inset-0 z-10 hidden items-center justify-center rounded-lg bg-[color:var(--ui-bg)]/70 group-data-[dragging=true]:flex">
            <span className="rounded-lg border border-dashed border-[color:var(--ui-border-active)] px-4 py-2 text-ui-sm font-medium text-[color:var(--ui-ink-accent)]">
              Drop to attach
            </span>
          </div>

          <Composer />
        </ComposerPrimitive.AttachmentDropzone>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}

function RoleTag({ children }: { children: ReactNode }) {
  return (
    <div className="mb-1 text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-accent)]">
      {children}
    </div>
  );
}

// --- part renderers ----------------------------------------------------------
// The model streams Markdown, so its text parts render through
// MarkdownTextPrimitive (GFM: tables, task lists, strikethrough). Block styling
// is themed with @carneirofc/ui tokens so it reads like the rest of the console;
// there's no Tailwind typography plugin here, hence the per-element components.
// `code` inside `pre` is neutralised so fenced blocks don't get the inline chip.
const MARKDOWN_COMPONENTS: Components = {
  p: ({ node, ...props }) => <p className="my-1.5 leading-relaxed first:mt-0 last:mb-0" {...props} />,
  a: ({ node, ...props }) => (
    <a
      className="text-[color:var(--ui-ink-accent)] underline underline-offset-2 hover:opacity-80"
      target="_blank"
      rel="noreferrer"
      {...props}
    />
  ),
  ul: ({ node, ...props }) => <ul className="my-1.5 list-disc pl-5" {...props} />,
  ol: ({ node, ...props }) => <ol className="my-1.5 list-decimal pl-5" {...props} />,
  li: ({ node, ...props }) => <li className="my-0.5" {...props} />,
  h1: ({ node, ...props }) => <h1 className="mb-1.5 mt-3 text-ui-base font-semibold first:mt-0" {...props} />,
  h2: ({ node, ...props }) => <h2 className="mb-1.5 mt-3 text-ui-sm font-semibold first:mt-0" {...props} />,
  h3: ({ node, ...props }) => <h3 className="mb-1 mt-2 text-ui-sm font-semibold first:mt-0" {...props} />,
  h4: ({ node, ...props }) => (
    <h4 className="mb-1 mt-2 text-ui-xs font-semibold uppercase tracking-wide first:mt-0" {...props} />
  ),
  code: ({ node, ...props }) => (
    <code
      className="rounded bg-[color:var(--ui-bg)] px-1 py-0.5 font-mono text-[0.85em] text-[color:var(--ui-ink)]"
      {...props}
    />
  ),
  pre: ({ node, ...props }) => (
    <pre
      className="my-2 overflow-x-auto rounded-md bg-[color:var(--ui-bg)] p-2 text-ui-xs [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-[color:var(--ui-ink)]"
      {...props}
    />
  ),
  blockquote: ({ node, ...props }) => (
    <blockquote
      className="my-2 border-l-2 border-ui pl-3 italic text-[color:var(--ui-ink-muted)]"
      {...props}
    />
  ),
  hr: ({ node, ...props }) => <hr className="my-3 border-ui" {...props} />,
  table: ({ node, ...props }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-ui-xs" {...props} />
    </div>
  ),
  th: ({ node, ...props }) => (
    <th className="border border-ui bg-[color:var(--ui-bg-soft)] px-2 py-1 text-left font-semibold" {...props} />
  ),
  td: ({ node, ...props }) => <td className="border border-ui px-2 py-1" {...props} />,
};

function MarkdownPart() {
  return <MarkdownTextPrimitive remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS} />;
}

function ReasoningPart({ text }: ReasoningMessagePartProps) {
  if (!text) return null;
  return (
    <details className="my-1 rounded-md border border-dashed border-ui bg-[color:var(--ui-bg-soft)] px-2 py-1">
      <summary className="cursor-pointer select-none text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
        Thinking
      </summary>
      <div className="mt-1 whitespace-pre-wrap break-words text-ui-xs italic text-[color:var(--ui-ink-muted)]">
        {text}
      </div>
    </details>
  );
}

function stringifyResult(result: unknown): string {
  if (result === undefined || result === null) return "";
  if (typeof result === "string") return result;
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

function ToolPart({ toolName, argsText, result, isError }: ToolCallMessagePartProps) {
  const running = result === undefined;
  const resultText = stringifyResult(result);
  return (
    <details
      className={`my-1 rounded-md border px-2 py-1 ${
        isError
          ? "border-[color:var(--ui-border-danger)] bg-[color:var(--ui-bg-danger)]"
          : "border-ui bg-[color:var(--ui-bg-soft)]"
      }`}
    >
      <summary className="flex cursor-pointer select-none items-center gap-2 text-ui-2xs">
        <span className="font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
          {isError ? "Tool error" : "Tool"}
        </span>
        <span className="font-mono text-[color:var(--ui-ink-accent)]">{toolName}</span>
        {running ? (
          <span className="text-[color:var(--ui-ink-subtle)]">· running…</span>
        ) : null}
      </summary>
      {argsText && argsText !== "{}" ? (
        <pre className="mt-1 max-h-40 overflow-auto rounded bg-[color:var(--ui-bg)] p-2 text-ui-2xs text-[color:var(--ui-ink-muted)]">
          {argsText}
        </pre>
      ) : null}
      {resultText ? (
        <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded bg-[color:var(--ui-bg)] p-2 text-ui-2xs text-[color:var(--ui-ink)]">
          {resultText}
        </pre>
      ) : null}
    </details>
  );
}

function ImagePart({ image, filename }: ImageMessagePartProps) {
  // eslint-disable-next-line @next/next/no-img-element
  return (
    <img
      src={image}
      alt={filename ?? "image"}
      className="mt-2 max-w-full rounded-md border border-ui"
    />
  );
}

function FilePart({ filename, data, mimeType }: FileMessagePartProps) {
  return (
    <a
      href={data}
      download={filename ?? "file"}
      className="mt-2 inline-flex items-center gap-1 rounded-md border border-ui bg-[color:var(--ui-bg-soft)] px-2 py-1 text-ui-xs text-[color:var(--ui-ink-accent)] hover:bg-[color:var(--ui-bg)]"
      title={mimeType}
    >
      📎 {filename ?? "file"}
    </a>
  );
}

const ASSISTANT_PART_COMPONENTS = {
  Text: MarkdownPart,
  Reasoning: ReasoningPart,
  Image: ImagePart,
  File: FilePart,
  tools: { Fallback: ToolPart },
} as const;

// --- attachment tiles --------------------------------------------------------
// One component for both the composer (pending) and the sent message: image
// attachments render as an inline thumbnail, everything else as a labeled chip.
// Removability is derived from the attachment source (composer = removable).
function AttachmentTile() {
  const attachment = useAttachment();
  const removable = attachment.source !== "message";
  const imageSrc =
    attachment.type === "image"
      ? attachment.content?.find((p) => p.type === "image")?.image
      : undefined;

  return (
    <AttachmentPrimitive.Root className="relative inline-flex max-w-full items-center">
      {imageSrc ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={imageSrc}
          alt={attachment.name}
          title={attachment.name}
          className="h-20 w-20 rounded-md border border-ui object-cover"
        />
      ) : (
        <span className="inline-flex items-center gap-1 rounded-md border border-ui bg-[color:var(--ui-bg-soft)] px-2 py-1 text-ui-2xs text-[color:var(--ui-ink-muted)]">
          <span>📎</span>
          <span className="max-w-[10rem] truncate">
            <AttachmentPrimitive.Name />
          </span>
        </span>
      )}
      {removable ? (
        <AttachmentPrimitive.Remove
          className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full border border-ui bg-[color:var(--ui-bg)] text-ui-2xs text-[color:var(--ui-ink-subtle)] hover:text-[color:var(--ui-ink-danger)]"
          aria-label="Remove attachment"
        >
          ✕
        </AttachmentPrimitive.Remove>
      ) : null}
    </AttachmentPrimitive.Root>
  );
}

// --- messages ----------------------------------------------------------------
function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex flex-col items-end">
      <div className="max-w-[85%] rounded-lg border border-accent-cyan/40 bg-[color:var(--ui-bg-info)] px-3 py-2">
        <RoleTag>You</RoleTag>
        <div className="mb-1 flex flex-wrap gap-2 empty:mb-0">
          <MessagePrimitive.Attachments components={{ Attachment: AttachmentTile }} />
        </div>
        <div className="whitespace-pre-wrap break-words text-ui-sm text-[color:var(--ui-ink)]">
          <MessagePrimitive.Parts />
        </div>
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="flex justify-start">
      <div className="max-w-[85%] rounded-lg border border-ui bg-[color:var(--ui-bg-soft)] px-3 py-2">
        <RoleTag>MAGI</RoleTag>
        <div className="break-words text-ui-sm text-[color:var(--ui-ink)]">
          <MessagePrimitive.Parts components={ASSISTANT_PART_COMPONENTS} />
        </div>
      </div>
    </MessagePrimitive.Root>
  );
}

function Composer() {
  const isRunning = useThread((t) => t.isRunning);

  return (
    <ComposerPrimitive.Root className="flex flex-col gap-2 border-t border-ui bg-[color:var(--ui-bg-soft)] px-3 py-3">
      <div className="flex flex-wrap gap-2 empty:hidden">
        <ComposerPrimitive.Attachments components={{ Attachment: AttachmentTile }} />
      </div>
      <div className="flex items-end gap-2">
        <ComposerPrimitive.AddAttachment asChild>
          <OutlineButton controlSize="md" type="button" title="Attach an image or file">
            📎
          </OutlineButton>
        </ComposerPrimitive.AddAttachment>
        {/* Voice → text. Mic when idle, stop while dictating (assistant-ui only
            renders StopDictation when a session is active). */}
        <ComposerPrimitive.If dictation={false}>
          <ComposerPrimitive.Dictate asChild>
            <OutlineButton controlSize="md" type="button" title="Dictate (voice to text)">
              🎤
            </OutlineButton>
          </ComposerPrimitive.Dictate>
        </ComposerPrimitive.If>
        <ComposerPrimitive.If dictation={true}>
          <ComposerPrimitive.StopDictation asChild>
            <OutlineButton variant="accent" controlSize="md" type="button" title="Stop dictation">
              ⏹ Rec
            </OutlineButton>
          </ComposerPrimitive.StopDictation>
        </ComposerPrimitive.If>
        <div className="flex min-h-[2.25rem] flex-1 flex-col justify-end">
          <ComposerPrimitive.If dictation={true}>
            <ComposerPrimitive.DictationTranscript className="px-1 pb-1 text-ui-2xs italic text-[color:var(--ui-ink-subtle)]" />
          </ComposerPrimitive.If>
          <ComposerPrimitive.Input
            placeholder="Message the brain…  (Enter to send, Shift+Enter for a newline)"
            className="max-h-40 min-h-[2.25rem] w-full resize-none rounded-lg border border-ui bg-[color:var(--ui-bg)] px-3 py-2 text-ui-sm text-[color:var(--ui-ink)] outline-none focus:border-[color:var(--ui-border-active)]"
          />
        </div>
        {isRunning ? (
          <ComposerPrimitive.Cancel asChild>
            <OutlineButton controlSize="md" type="button">
              Stop
            </OutlineButton>
          </ComposerPrimitive.Cancel>
        ) : (
          <ComposerPrimitive.Send asChild>
            <OutlineButton variant="accent" controlSize="md" type="button">
              Send
            </OutlineButton>
          </ComposerPrimitive.Send>
        )}
      </div>
    </ComposerPrimitive.Root>
  );
}
