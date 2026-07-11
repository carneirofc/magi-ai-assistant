// Client-side session registry for the chat console. The chat-api keeps each
// conversation's memory server-side keyed by session id, but has no endpoint to
// enumerate sessions — so the *list* of conversations lives here, in localStorage,
// per browser. Each entry is just an id + a derived title + timestamps; switching
// sessions re-keys the runtime (a fresh, empty transcript) while the assistant still
// remembers that id's history on its side.
//
// All helpers are pure: they take the current registry and return a new one. The
// component holds it in state and persists with `saveRegistry` after each change.

export interface ChatSession {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  /** Sticks to the top of the rail. Optional: pre-polish registries load as-is. */
  pinned?: boolean;
  /** Hidden from the default rail (recoverable from its Archived section). */
  archived?: boolean;
}

export interface SessionRegistry {
  activeId: string;
  /** Most-recently-updated first (kept sorted by `saveRegistry` callers). */
  sessions: ChatSession[];
}

const KEY = "magi.chat.sessions.v1";
// The pre-registry single-session key (see the old ChatConsole) — migrated once so
// an in-flight conversation isn't orphaned by the upgrade.
const LEGACY_SESSION_KEY = "magi.chat.sessionId";
export const DEFAULT_TITLE = "New chat";
const TITLE_MAX = 48;

/** A timestamp; wrapped so the one impure call is easy to find. */
function nowMs(): number {
  return Date.now();
}

export function newSessionId(): string {
  return `web-${crypto.randomUUID()}`;
}

function makeSession(id?: string): ChatSession {
  const t = nowMs();
  return { id: id ?? newSessionId(), title: DEFAULT_TITLE, createdAt: t, updatedAt: t };
}

/** A short title from the first user message: first line, trimmed and capped. */
export function deriveTitle(text: string): string {
  const line = text.trim().split("\n", 1)[0].trim();
  if (!line) return DEFAULT_TITLE;
  return line.length > TITLE_MAX ? `${line.slice(0, TITLE_MAX - 1)}…` : line;
}

/** Load the registry, migrating the legacy key and guaranteeing a valid active
 * session. Client-only (touches localStorage) — call from an effect. */
export function loadRegistry(): SessionRegistry {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<SessionRegistry>;
      if (parsed && Array.isArray(parsed.sessions) && parsed.sessions.length > 0) {
        const sessions = parsed.sessions;
        const activeId = sessions.some((s) => s.id === parsed.activeId)
          ? (parsed.activeId as string)
          : sessions[0].id;
        return { activeId, sessions };
      }
    }
    // First run under the registry: adopt the legacy single session if present.
    const legacy = localStorage.getItem(LEGACY_SESSION_KEY);
    const session = makeSession(legacy || undefined);
    const reg: SessionRegistry = { activeId: session.id, sessions: [session] };
    saveRegistry(reg);
    return reg;
  } catch {
    const session = makeSession();
    return { activeId: session.id, sessions: [session] };
  }
}

export function saveRegistry(reg: SessionRegistry): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(reg));
  } catch {
    /* storage full / unavailable — the in-memory registry still works this session */
  }
}

export function activeSession(reg: SessionRegistry): ChatSession | undefined {
  return reg.sessions.find((s) => s.id === reg.activeId);
}

/** Start a fresh conversation and make it active. */
export function createSession(reg: SessionRegistry): SessionRegistry {
  const session = makeSession();
  return { activeId: session.id, sessions: [session, ...reg.sessions] };
}

export function selectSession(reg: SessionRegistry, id: string): SessionRegistry {
  if (!reg.sessions.some((s) => s.id === id) || id === reg.activeId) return reg;
  return { ...reg, activeId: id };
}

export function renameSession(reg: SessionRegistry, id: string, title: string): SessionRegistry {
  const clean = title.trim().slice(0, TITLE_MAX) || DEFAULT_TITLE;
  return {
    ...reg,
    sessions: reg.sessions.map((s) => (s.id === id ? { ...s, title: clean } : s)),
  };
}

/** Delete a session, keeping the invariant that one always exists and is active. */
export function removeSession(reg: SessionRegistry, id: string): SessionRegistry {
  const remaining = reg.sessions.filter((s) => s.id !== id);
  if (remaining.length === 0) {
    const session = makeSession();
    return { activeId: session.id, sessions: [session] };
  }
  const activeId = reg.activeId === id ? remaining[0].id : reg.activeId;
  return { activeId, sessions: remaining };
}

/** Record activity on a session: bump `updatedAt`, move it to the front, and give
 * it a real title from the first user message if it's still "New chat". */
export function touchSession(
  reg: SessionRegistry,
  id: string,
  firstUserText: string,
): SessionRegistry {
  const target = reg.sessions.find((s) => s.id === id);
  if (!target) return reg;
  const title =
    target.title === DEFAULT_TITLE && firstUserText.trim()
      ? deriveTitle(firstUserText)
      : target.title;
  const updated: ChatSession = { ...target, title, updatedAt: nowMs() };
  return {
    ...reg,
    sessions: [updated, ...reg.sessions.filter((s) => s.id !== id)],
  };
}

// --- session polish: pin / archive / rail ordering ---------------------------
// `pinned` and `archived` are optional so registries saved before this feature
// load unchanged. Pinned sessions sort to the top of the rail; archived ones
// leave the default list (recoverable from the rail's Archived section).

export function togglePinSession(reg: SessionRegistry, id: string): SessionRegistry {
  return {
    ...reg,
    sessions: reg.sessions.map((s) => (s.id === id ? { ...s, pinned: !s.pinned } : s)),
  };
}

/** Archive/unarchive. Archiving the active session activates the most recent
 * visible one (creating a fresh session when none is left). */
export function toggleArchiveSession(reg: SessionRegistry, id: string): SessionRegistry {
  const sessions = reg.sessions.map((s) =>
    s.id === id ? { ...s, archived: !s.archived, pinned: false } : s,
  );
  let activeId = reg.activeId;
  if (activeId === id && sessions.find((s) => s.id === id)?.archived) {
    const visible = sessions.filter((s) => !s.archived);
    if (visible.length === 0) {
      const fresh = { id: newSessionId(), title: "New chat", createdAt: Date.now(), updatedAt: Date.now() };
      return { activeId: fresh.id, sessions: [fresh, ...sessions] };
    }
    activeId = visible[0].id;
  }
  return { activeId, sessions };
}

/** The rail's default list: pinned first (each group most-recent first). */
export function visibleSessions(reg: SessionRegistry): ChatSession[] {
  const shown = reg.sessions.filter((s) => !s.archived);
  return [...shown.filter((s) => s.pinned), ...shown.filter((s) => !s.pinned)];
}

export function archivedSessions(reg: SessionRegistry): ChatSession[] {
  return reg.sessions.filter((s) => s.archived);
}
