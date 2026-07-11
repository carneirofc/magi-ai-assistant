"use client";

// The chat console: an operator playground for talking to the running assistant over
// streaming SSE. Built on assistant-ui's LocalRuntime (state, streaming, auto-
// scroll, cancel come for free) but rendered with unstyled primitives themed via
// @carneirofc/ui tokens so it matches the rest of the dashboard.
//
// Feature-rich by design: sender avatars flank each turn; the composer takes image
// + file attachments (drag-drop, dictation, click any image to zoom); the transcript
// renders live *thinking* (reasoning) and *tool activity* (tool cards) as they
// stream; assistant Markdown renders GFM, syntax-highlights ```lang fences (Shiki,
// see CodeBlock.tsx) and draws `mermaid` fences as diagrams; reply media renders
// inline (images) or as links (files); selecting text in a reply offers a "Quote"
// action that cites it in the next turn; and a context-window meter in the composer
// footer reports each turn's token usage (streamed on the `done` frame).
//
// The `user_id` scopes durable memory (chat "as" any user to test their memory).
// Conversations are tracked client-side (chat-sessions.ts): the session rail lists
// them, switching re-keys ChatThread → a fresh runtime with an empty transcript,
// while the assistant still remembers that session id server-side. The bearer token
// stays server-side; see api/chat/route.ts.

import {
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";
import {
  AssistantRuntimeProvider,
  AttachmentPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  SelectionToolbarPrimitive,
  ThreadPrimitive,
  useAttachment,
  useLocalRuntime,
  useMessageQuote,
  useThread,
  type FileMessagePartProps,
  type ImageMessagePartProps,
  type ReasoningMessagePartProps,
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import type { CodeHeaderProps, SyntaxHighlighterProps } from "@assistant-ui/react-markdown";
import type { ComponentType } from "react";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { OutlineButton, TextInput } from "@carneirofc/ui";

import { createChatModelAdapter, type ChatUsage } from "../lib/chat-adapter";
import { greetIfFresh } from "../lib/chat-greeting";
import { MoodScope, useMood, useMoodAdapterEvents } from "../lib/chat-mood";
import { createChatAttachmentAdapter } from "../lib/chat-attachments";
import { createDictationAdapter, dictationSupported } from "../lib/chat-dictation";
import { ContextDisplay, type ThreadTokenUsage } from "./assistant-ui/context-display";
import { CodeHeader, CodeSyntaxHighlighter } from "./CodeBlock";
import { MermaidDiagram } from "./MermaidDiagram";
import { clearSessionHistory, createSessionHistoryAdapter } from "../lib/chat-history";
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
} from "../lib/chat-sessions";

const USER_KEY = "magi.chat.userId";
const RAIL_KEY = "magi.chat.railCollapsed";
const DEFAULT_USER = "console";

// The active user id, so user-message avatars can show whose turn it is (the
// message parts assistant-ui hands to a renderer don't carry it).
const UserIdContext = createContext<string>(DEFAULT_USER);

// The bot's presented identity (name + avatar URL), so the assistant's face and
// name match what's configured on the Identity page. Both null → default glyph.
type ChatIdentity = { avatarUrl: string | null; name: string | null };
const IdentityContext = createContext<ChatIdentity>({ avatarUrl: null, name: null });

export type ChatConsoleProps = {
  /** Pin every turn to this user id: the id input disappears and localStorage is
   * ignored — the companion surface chats as one configured person, while the
   * operator console (no prop) keeps its free switcher for testing. */
  pinnedUserId?: string | null;
  /** Greet-on-open: when the active conversation is brand new (empty
   * transcript — including "New chat"), the assistant speaks first via the
   * engine's greeting turn. Resuming an ongoing conversation never greets.
   * See lib/chat-greeting.ts. */
  greetOnOpen?: boolean;
};

export function ChatConsole({
  pinnedUserId = null,
  greetOnOpen = false,
}: ChatConsoleProps = {}) {
  const [userId, setUserId] = useState(pinnedUserId || DEFAULT_USER);
  // Null until mounted so the first client render matches the server (no crypto /
  // localStorage during SSR → no hydration mismatch).
  const [registry, setRegistry] = useState<SessionRegistry | null>(null);
  const [identity, setIdentity] = useState<ChatIdentity>({ avatarUrl: null, name: null });
  // Whether the conversation rail is collapsed to a slim strip. Persisted so the
  // operator's preference survives reloads.
  const [railCollapsed, setRailCollapsed] = useState(false);
  // Bumped when a greeting lands, so the thread remounts and its history
  // adapter restores the seeded greeting (see GreetOnOpen below).
  const [greetEpoch, setGreetEpoch] = useState(0);
  const onGreeted = useCallback(() => setGreetEpoch((e) => e + 1), []);

  useEffect(() => {
    if (!pinnedUserId) setUserId(localStorage.getItem(USER_KEY) || DEFAULT_USER);
    setRegistry(loadRegistry());
    setRailCollapsed(localStorage.getItem(RAIL_KEY) === "1");
  }, [pinnedUserId]);

  function toggleRail() {
    setRailCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem(RAIL_KEY, next ? "1" : "0");
      return next;
    });
  }

  // Load the configured bot identity once; the assistant avatar/name follow it.
  // The version rides the avatar URL as a cache-buster so an edit shows up on reload.
  useEffect(() => {
    let active = true;
    fetch("/api/identity")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { display_name?: string; has_avatar?: boolean; version?: string } | null) => {
        if (!active || !d) return;
        setIdentity({
          avatarUrl: d.has_avatar
            ? `/api/identity/avatar?v=${encodeURIComponent(d.version ?? "")}`
            : null,
          name: d.display_name || null,
        });
      })
      .catch(() => {});
    return () => {
      active = false;
    };
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
  const greetKey = `${sessionId}:${greetEpoch}`;

  return (
    <IdentityContext.Provider value={identity}>
    {/* Mood signal: join the page's MoodProvider when one is mounted (the
        companion layout shares it with its stage), else scope our own so the
        composer's mood badge still works standalone. */}
    <MoodScope>
    <div className="flex min-h-[520px] flex-1 flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        {pinnedUserId ? (
          <span />
        ) : (
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
        )}
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
          collapsed={railCollapsed}
          onToggleCollapsed={toggleRail}
          onNew={() => commit(createSession(registry))}
          onSelect={(id) => commit(selectSession(registry, id))}
          onRename={(id, title) => commit(renameSession(registry, id, title))}
          onRemove={(id) => {
            clearSessionHistory(id);
            commit(removeSession(registry, id));
          }}
        />

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-ui bg-[color:var(--ui-bg)]">
          {greetOnOpen ? (
            <GreetOnOpen sessionId={sessionId} userId={userId} onGreeted={onGreeted} />
          ) : null}
          <ChatThread
            key={greetKey}
            sessionId={sessionId}
            userId={userId}
            onUserSend={(text) => commit(touchSession(registry, sessionId, text))}
          />
        </div>
      </div>
    </div>
    </MoodScope>
    </IdentityContext.Provider>
  );
}

/** Runs the greet-on-open flow for the active session (renders nothing). Lives
 * inside the MoodScope so the greeting drives the same mood/lifecycle signal
 * the stage and badge react to. Each session id is attempted once per mount —
 * the transcript-empty check in greetIfFresh is the real policy gate. */
function GreetOnOpen({
  sessionId,
  userId,
  onGreeted,
}: {
  sessionId: string;
  userId: string;
  onGreeted: () => void;
}) {
  const mood = useMood();
  const { onMood, onLifecycle } = useMoodAdapterEvents(mood);
  const attempted = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!sessionId || !userId || attempted.current.has(sessionId)) return;
    attempted.current.add(sessionId);
    void greetIfFresh(sessionId, userId, { onMood, onLifecycle }).then((greeted) => {
      if (greeted) onGreeted();
    });
  }, [sessionId, userId, onMood, onLifecycle, onGreeted]);
  return null;
}

// --- session rail ------------------------------------------------------------
// A compact square control for the rail's chrome (collapse toggle, new-chat when
// collapsed). Shares the composer's icon-button feel but sized for the rail.
function RailIconButton({
  children,
  ...props
}: ComponentPropsWithoutRef<"button">) {
  return (
    <button
      type="button"
      className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-transparent text-[color:var(--ui-ink-muted)] transition-colors hover:border-ui hover:bg-[color:var(--ui-bg)] hover:text-[color:var(--ui-ink)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--ui-border-active)]"
      {...props}
    >
      {children}
    </button>
  );
}
function ChevronLeftIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M15 6l-6 6 6 6" />
    </svg>
  );
}
function ChevronRightIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

function SessionRail({
  registry,
  collapsed,
  onToggleCollapsed,
  onNew,
  onSelect,
  onRename,
  onRemove,
}: {
  registry: SessionRegistry;
  collapsed: boolean;
  onToggleCollapsed: () => void;
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

  // Collapsed: a slim strip with just the expand toggle and a new-chat button,
  // so the transcript gets the full width but the rail is one click away.
  if (collapsed) {
    return (
      <aside className="flex w-11 shrink-0 flex-col items-center gap-2 rounded-xl border border-ui bg-[color:var(--ui-bg-soft)] p-2">
        <RailIconButton onClick={onToggleCollapsed} title="Show conversations" aria-label="Show conversations">
          <ChevronRightIcon />
        </RailIconButton>
        <RailIconButton onClick={onNew} title="New chat" aria-label="New chat">
          <PlusIcon />
        </RailIconButton>
        <span
          className="mt-1 text-ui-2xs font-medium text-[color:var(--ui-ink-subtle)]"
          title={`${registry.sessions.length} conversation(s)`}
        >
          {registry.sessions.length}
        </span>
      </aside>
    );
  }

  return (
    <aside className="flex w-52 shrink-0 flex-col gap-2 overflow-hidden rounded-xl border border-ui bg-[color:var(--ui-bg-soft)] p-2">
      <div className="flex items-center gap-2">
        <OutlineButton variant="accent" controlSize="md" onClick={onNew} className="flex-1">
          + New chat
        </OutlineButton>
        <RailIconButton onClick={onToggleCollapsed} title="Hide conversations" aria-label="Hide conversations">
          <ChevronLeftIcon />
        </RailIconButton>
      </div>
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

  // Latest turn's token usage, surfaced by the context meter. Reset naturally per
  // session because the parent keys ChatThread on the session id (it remounts).
  const [usage, setUsage] = useState<ChatUsage | null>(null);
  // Voice-input support + the last dictation failure, so the mic can disable
  // itself where SpeechRecognition is missing and surface permission errors the
  // browser would otherwise swallow. `micSupported` starts false to keep SSR and
  // the first client render in sync (window isn't there during SSR).
  const [micSupported, setMicSupported] = useState(false);
  const [dictationError, setDictationError] = useState<string | null>(null);
  useEffect(() => setMicSupported(dictationSupported()), []);

  // The turn's mood + lifecycle, streamed into the ambient MoodScope so the
  // composer badge (and a surrounding persona stage) react as the reply starts.
  const mood = useMood();
  const { onMood, onLifecycle } = useMoodAdapterEvents(mood);

  const adapter = useMemo(
    () =>
      createChatModelAdapter(
        () => ({ sessionId, userId: userIdRef.current }),
        (text) => onSendRef.current(text),
        (next) => setUsage(next),
        onMood,
        onLifecycle,
      ),
    [sessionId, onMood, onLifecycle],
  );
  const attachments = useMemo(() => createChatAttachmentAdapter(), []);
  // Voice → text via the browser's Web Speech API (no server round-trip). The
  // wrapper reports a failed session (denied mic, no device) so the composer can
  // show it instead of the click looking like a no-op.
  const dictation = useMemo(
    () => createDictationAdapter((message) => setDictationError(message)),
    [],
  );
  // Persist this session's visible transcript so switching to it (or reloading)
  // restores the conversation, not an empty thread. Bound to this session id.
  const history = useMemo(() => createSessionHistoryAdapter(sessionId), [sessionId]);
  const runtime = useLocalRuntime(adapter, {
    adapters: { attachments, dictation, history },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
     <UserIdContext.Provider value={userId}>
      <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
        {/* Select text in any message to pop a "Quote" action; it captures the
            span into the composer, cited back to the assistant on the next send. */}
        <SelectionToolbarPrimitive.Root className="z-50 flex items-center overflow-hidden rounded-lg border border-ui bg-[color:var(--ui-bg)] shadow-lg">
          <SelectionToolbarPrimitive.Quote className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-ui-2xs font-medium text-[color:var(--ui-ink)] hover:bg-[color:var(--ui-bg-soft)]">
            <QuoteIcon />
            Quote
          </SelectionToolbarPrimitive.Quote>
        </SelectionToolbarPrimitive.Root>

        {/* Drop images/files anywhere over the conversation to attach them. */}
        <ComposerPrimitive.AttachmentDropzone className="group relative flex min-h-0 flex-1 flex-col rounded-lg outline-2 -outline-offset-2 outline-[color:var(--ui-border-active)] data-[dragging=true]:outline-dashed">
          <ThreadPrimitive.Viewport className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
            <ThreadPrimitive.Empty>
              <div className="m-auto flex max-w-sm flex-col items-center gap-1 text-center">
                <p className="text-ui-sm font-medium text-[color:var(--ui-ink-muted)]">
                  Talk to the assistant
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

          <Composer
            usage={usage}
            micSupported={micSupported}
            dictationError={dictationError}
            onDismissDictationError={() => setDictationError(null)}
          />
        </ComposerPrimitive.AttachmentDropzone>
      </ThreadPrimitive.Root>
     </UserIdContext.Provider>
    </AssistantRuntimeProvider>
  );
}

// --- avatars -----------------------------------------------------------------
// A round sender chip beside each message. The user's shows the first letter of
// the active user id (so "chatting as" someone is visible at a glance); the
// assistant's is the configured profile picture (Identity page), falling back to
// the assistant's initial in accent colors when none is set.
function Avatar({ kind }: { kind: "user" | "assistant" }) {
  const userId = useContext(UserIdContext);
  const identity = useContext(IdentityContext);
  const isUser = kind === "user";
  const initial = (userId.trim()[0] || "?").toUpperCase();
  const assistantName = identity.name || "MAGI";

  if (!isUser && identity.avatarUrl) {
    return (
      <div
        className="h-7 w-7 shrink-0 select-none overflow-hidden rounded-full border border-ui"
        title={assistantName}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- BFF-served, dynamic src */}
        <img src={identity.avatarUrl} alt="" className="h-full w-full object-cover" />
      </div>
    );
  }

  return (
    <div
      className={`flex h-7 w-7 shrink-0 select-none items-center justify-center rounded-full border text-ui-2xs font-semibold ${
        isUser
          ? "border-accent-cyan/40 bg-[color:var(--ui-bg-info)] text-[color:var(--ui-ink)]"
          : "border-ui bg-[color:var(--ui-bg-accent,var(--ui-bg-soft))] text-[color:var(--ui-ink-accent)]"
      }`}
      title={isUser ? `You — ${userId}` : assistantName}
      aria-hidden
    >
      {isUser ? initial : (assistantName.trim()[0] || "M").toUpperCase()}
    </div>
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
// `SyntaxHighlighter` + `CodeHeader` take over ```lang fences (Shiki-highlighted
// card); the plain `pre`/`code` below still cover inline code and bare ``` fences.
type MarkdownComponents = Components & {
  SyntaxHighlighter?: ComponentType<SyntaxHighlighterProps>;
  CodeHeader?: ComponentType<CodeHeaderProps>;
};
const MARKDOWN_COMPONENTS: MarkdownComponents = {
  SyntaxHighlighter: CodeSyntaxHighlighter,
  CodeHeader,
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

// ```mermaid fences render as diagrams (ChatConsole → MermaidDiagram, which draws
// its own header/toggle, so the default CodeHeader is suppressed here); every other
// language flows through the Shiki highlighter + CodeHeader in MARKDOWN_COMPONENTS.
const NoCodeHeader: ComponentType<CodeHeaderProps> = () => null;
const MARKDOWN_BY_LANGUAGE = {
  mermaid: { SyntaxHighlighter: MermaidDiagram, CodeHeader: NoCodeHeader },
} as const;

function MarkdownPart() {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={[remarkGfm]}
      components={MARKDOWN_COMPONENTS}
      componentsByLanguage={MARKDOWN_BY_LANGUAGE}
    />
  );
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

// An image that opens a full-viewport lightbox on click — shared by reply images
// and composer/message image attachments so any picture in the console can be
// inspected at size. The overlay closes on click or Escape.
function ZoomableImage({
  src,
  alt,
  className,
}: {
  src: string;
  alt?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt={alt ?? "image"}
        onClick={() => setOpen(true)}
        className={`cursor-zoom-in ${className ?? ""}`}
      />
      {open ? (
        <div
          className="fixed inset-0 z-50 flex cursor-zoom-out items-center justify-center bg-black/75 p-6"
          onClick={() => setOpen(false)}
          role="dialog"
          aria-modal="true"
          aria-label={alt ?? "image preview"}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={src}
            alt={alt ?? "image"}
            className="max-h-full max-w-full rounded-md shadow-2xl"
          />
        </div>
      ) : null}
    </>
  );
}

function ImagePart({ image, filename }: ImageMessagePartProps) {
  return (
    <ZoomableImage
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
        <ZoomableImage
          src={imageSrc}
          alt={attachment.name}
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
// The span the user quoted from a prior reply, shown atop their turn (and prepended
// as context to the outgoing message; see chat-adapter.ts). Reads the same
// `metadata.custom.quote` the composer wrote on send.
function QuotedContext() {
  const quote = useMessageQuote();
  if (!quote?.text) return null;
  return (
    <blockquote className="mb-1.5 max-h-24 overflow-hidden border-l-2 border-accent-cyan/50 pl-2 text-ui-2xs italic text-[color:var(--ui-ink-muted)]">
      {quote.text}
    </blockquote>
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex items-start justify-end gap-2">
      <div className="max-w-[85%] rounded-lg border border-accent-cyan/40 bg-[color:var(--ui-bg-info)] px-3 py-2">
        <RoleTag>You</RoleTag>
        <QuotedContext />
        <div className="mb-1 flex flex-wrap gap-2 empty:mb-0">
          <MessagePrimitive.Attachments components={{ Attachment: AttachmentTile }} />
        </div>
        <div className="whitespace-pre-wrap break-words text-ui-sm text-[color:var(--ui-ink)]">
          <MessagePrimitive.Parts />
        </div>
      </div>
      <Avatar kind="user" />
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  const identity = useContext(IdentityContext);
  return (
    <MessagePrimitive.Root className="flex items-start justify-start gap-2">
      <Avatar kind="assistant" />
      <div className="max-w-[85%] rounded-lg border border-ui bg-[color:var(--ui-bg-soft)] px-3 py-2">
        <RoleTag>{identity.name || "MAGI"}</RoleTag>
        <div className="break-words text-ui-sm text-[color:var(--ui-ink)]">
          <MessagePrimitive.Parts components={ASSISTANT_PART_COMPONENTS} />
        </div>
      </div>
    </MessagePrimitive.Root>
  );
}

// --- composer icons ----------------------------------------------------------
// Line icons (currentColor) for the toolbar so the controls read like a modern
// chat composer rather than emoji glyphs.
function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}
function QuoteIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" className={className} aria-hidden>
      <path d="M7.5 6C5.6 6 4 7.6 4 9.5S5.6 13 7.5 13c.2 0 .3 0 .5-.1-.3 1.4-1.4 2.5-2.8 2.9-.4.1-.6.5-.5.9.1.3.4.5.7.5h.2c2.6-.6 4.4-2.9 4.4-5.6V9.5C10 7.6 8.4 6 6.5 6h1zm9 0C14.6 6 13 7.6 13 9.5s1.6 3.5 3.5 3.5c.2 0 .3 0 .5-.1-.3 1.4-1.4 2.5-2.8 2.9-.4.1-.6.5-.5.9.1.3.4.5.7.5h.2c2.6-.6 4.4-2.9 4.4-5.6V9.5C19 7.6 17.4 6 15.5 6h1z" />
    </svg>
  );
}
function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="9" y="2" width="6" height="12" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </svg>
  );
}
function StopIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden>
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  );
}
function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 19V5M5 12l7-7 7 7" />
    </svg>
  );
}

// A round, icon-sized control used across the composer toolbar. forwardRef +
// prop spread so assistant-ui's `asChild` primitives (Radix Slot) can drive it.
const ICON_BUTTON_BASE =
  "inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--ui-border-active)] disabled:cursor-not-allowed disabled:opacity-40";
const ICON_BUTTON_VARIANTS = {
  ghost:
    "border-transparent text-[color:var(--ui-ink-muted)] hover:border-ui hover:bg-[color:var(--ui-bg-soft)] hover:text-[color:var(--ui-ink)]",
  accent:
    "border-transparent bg-[color:var(--ui-ink-accent)] text-[color:var(--ui-bg)] hover:opacity-90",
  danger:
    "border-[color:var(--ui-border-danger)] bg-[color:var(--ui-bg-danger)] text-[color:var(--ui-ink-danger)] hover:opacity-90",
} as const;

type IconButtonProps = ComponentPropsWithoutRef<"button"> & {
  variant?: keyof typeof ICON_BUTTON_VARIANTS;
};
const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ variant = "ghost", className, type = "button", ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={`${ICON_BUTTON_BASE} ${ICON_BUTTON_VARIANTS[variant]} ${className ?? ""}`}
      {...props}
    />
  ),
);
IconButton.displayName = "IconButton";

function Composer({
  usage,
  micSupported,
  dictationError,
  onDismissDictationError,
}: {
  usage: ChatUsage | null;
  micSupported: boolean;
  dictationError: string | null;
  onDismissDictationError: () => void;
}) {
  const isRunning = useThread((t) => t.isRunning);

  return (
    <ComposerPrimitive.Root className="flex flex-col gap-2 border-t border-ui bg-[color:var(--ui-bg-soft)] px-3 py-3">
      {/* One integrated field: attachments, transcript, textarea, and a toolbar
          row all live inside a single rounded surface (Claude-style). */}
      <div className="flex flex-col gap-1.5 rounded-2xl border border-ui bg-[color:var(--ui-bg)] px-2 py-2 transition-colors focus-within:border-[color:var(--ui-border-active)]">
        {/* Pending quote captured from a reply — previewed here until sent or
            dismissed (assistant-ui only renders this when a quote is set). */}
        <ComposerPrimitive.Quote className="mx-1 mt-1 flex items-start gap-2 rounded-lg border-l-2 border-accent-cyan/50 bg-[color:var(--ui-bg-soft)] px-2 py-1">
          <QuoteIcon className="mt-0.5 shrink-0 text-[color:var(--ui-ink-subtle)]" />
          <ComposerPrimitive.QuoteText className="line-clamp-3 min-w-0 flex-1 text-ui-2xs italic text-[color:var(--ui-ink-muted)]" />
          <ComposerPrimitive.QuoteDismiss
            className="shrink-0 rounded text-ui-2xs text-[color:var(--ui-ink-subtle)] hover:text-[color:var(--ui-ink-danger)]"
            aria-label="Remove quote"
          >
            ✕
          </ComposerPrimitive.QuoteDismiss>
        </ComposerPrimitive.Quote>

        <div className="flex flex-wrap gap-2 px-1 pt-1 empty:hidden">
          <ComposerPrimitive.Attachments components={{ Attachment: AttachmentTile }} />
        </div>

        {/* Live dictation transcript sits above the input while recording. */}
        <ComposerPrimitive.If dictation={true}>
          <ComposerPrimitive.DictationTranscript className="px-2 text-ui-2xs italic text-[color:var(--ui-ink-subtle)]" />
        </ComposerPrimitive.If>

        <ComposerPrimitive.Input
          placeholder="Message the assistant…  (Enter to send, Shift+Enter for a newline)"
          className="max-h-40 min-h-[2.5rem] w-full resize-none bg-transparent px-2 py-1.5 text-ui-sm text-[color:var(--ui-ink)] outline-none placeholder:text-[color:var(--ui-ink-subtle)]"
        />

        <div className="flex items-center gap-1">
          <ComposerPrimitive.AddAttachment asChild>
            <IconButton title="Attach an image or file" aria-label="Attach an image or file">
              <PlusIcon />
            </IconButton>
          </ComposerPrimitive.AddAttachment>

          {/* Voice → text. Mic when idle, stop while dictating (assistant-ui only
              renders StopDictation when a session is active). Where the browser
              has no SpeechRecognition, the mic is present but disabled. */}
          {micSupported ? (
            <>
              <ComposerPrimitive.If dictation={false}>
                <ComposerPrimitive.Dictate asChild>
                  <IconButton
                    title="Dictate (voice to text)"
                    aria-label="Dictate (voice to text)"
                    onClick={onDismissDictationError}
                  >
                    <MicIcon />
                  </IconButton>
                </ComposerPrimitive.Dictate>
              </ComposerPrimitive.If>
              <ComposerPrimitive.If dictation={true}>
                <ComposerPrimitive.StopDictation asChild>
                  <IconButton
                    variant="danger"
                    className="animate-pulse"
                    title="Stop dictation"
                    aria-label="Stop dictation"
                  >
                    <StopIcon />
                  </IconButton>
                </ComposerPrimitive.StopDictation>
              </ComposerPrimitive.If>
            </>
          ) : (
            <IconButton
              disabled
              title="Dictation needs a browser with speech recognition (Chrome, Edge, or Safari)"
              aria-label="Dictation unavailable in this browser"
            >
              <MicIcon />
            </IconButton>
          )}

          <div className="ml-auto flex items-center gap-2">
            {isRunning ? (
              <ComposerPrimitive.Cancel asChild>
                <IconButton variant="danger" title="Stop generating" aria-label="Stop generating">
                  <StopIcon />
                </IconButton>
              </ComposerPrimitive.Cancel>
            ) : (
              <ComposerPrimitive.Send asChild>
                <IconButton variant="accent" title="Send message" aria-label="Send message">
                  <SendIcon />
                </IconButton>
              </ComposerPrimitive.Send>
            )}
          </div>
        </div>
      </div>

      {/* Footer: a dictation error (dismissible) on the left, the turn's mood +
          the live context-window fill for the last turn on the right. Hidden
          until either has something to show, so a fresh thread stays clean. */}
      <div className="flex min-h-[1rem] items-center justify-between gap-3 empty:hidden">
        {dictationError ? (
          <button
            type="button"
            onClick={onDismissDictationError}
            className="flex items-center gap-1.5 text-left text-ui-2xs text-[color:var(--ui-ink-danger)] hover:opacity-80"
            title="Dismiss"
          >
            <span aria-hidden>⚠</span>
            <span>{dictationError}</span>
            <span aria-hidden className="opacity-60">✕</span>
          </button>
        ) : (
          <span />
        )}
        <div className="flex items-center gap-3">
          <MoodBadge />
          {/* assistant-ui's ContextDisplay, fed our streamed usage (bar tracks
              total/context window; hover breaks the turn down). Shown only once a
              reply reports a context window, so a fresh thread stays clean. */}
          {usage && usage.contextWindow ? (
            <div className="flex items-center gap-2 text-ui-2xs text-[color:var(--ui-ink-subtle)]">
              <span className="font-medium uppercase tracking-wide">Context</span>
              <ContextDisplay.Bar
                modelContextWindow={usage.contextWindow}
                usage={toTokenUsage(usage)}
                side="top"
              />
            </div>
          ) : null}
        </div>
      </div>
    </ComposerPrimitive.Root>
  );
}

/** The turn's streamed mood (the engine's pre-reply pass), as a tiny footer
 * badge — the console's always-on view of the signal the companion stage
 * animates. Hidden until the first moody turn, so plain engines stay clean;
 * while a turn is in flight the lifecycle phase rides along. */
function MoodBadge() {
  const { mood, lifecycle } = useMood();
  if (!mood) return null;
  const busy = lifecycle !== "idle" && lifecycle !== "error";
  return (
    <span
      className="flex items-center gap-1.5 text-ui-2xs text-[color:var(--ui-ink-subtle)]"
      title="The reply's delivery mood, predicted by the engine before it answers"
    >
      <span className="font-medium uppercase tracking-wide">Mood</span>
      <span className="font-mono text-[color:var(--ui-ink-accent)]">{mood}</span>
      {busy ? <span className="opacity-60">· {lifecycle}</span> : null}
    </span>
  );
}

/** Map our streamed per-turn `ChatUsage` onto the shape ContextDisplay expects
 * (assistant-ui's `ThreadTokenUsage`); `contextWindow` rides the component's
 * `modelContextWindow` prop, not the usage object. */
function toTokenUsage(usage: ChatUsage): ThreadTokenUsage {
  return {
    inputTokens: usage.inputTokens,
    outputTokens: usage.outputTokens,
    totalTokens: usage.totalTokens,
    cachedInputTokens: usage.cachedTokens,
    reasoningTokens: usage.reasoningTokens,
  };
}
