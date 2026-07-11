"use client";

// The chat's mood + lifecycle signal, lifted out of the runtime so anything on
// the page — a persona stage beside the transcript, a badge in the composer —
// can react to the turn as it unfolds.
//
// Two inputs, both driven by the chat adapter (see chat-adapter.ts):
//   mood      — the engine's pre-reply mood pass (the SSE `meta` frame): one
//               name from the deployment's vocabulary, arriving BEFORE the
//               first content token. Null until the first moody turn; kept
//               across turns (the face holds its last expression while the
//               next reply is being thought up).
//   lifecycle — where the current turn is: idle → thinking (request sent) →
//               streaming (first delta) ⇄ tool (a tool call is running) →
//               idle, or error. Purely client-observed; works against engines
//               with no mood pass at all.
//
// Composition contract: wrap the chat surface in <MoodProvider> to share the
// signal (the companion layout does this so its stage sees the console's
// stream). Without a provider the console still works — it falls back to
// component-local state via `useMoodBridge`.

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

/** Where the current turn is, as observed by the chat adapter. */
export type ChatLifecycle = "idle" | "thinking" | "streaming" | "tool" | "error";

export type MoodState = {
  /** The engine's streamed mood for the latest turn (null before the first). */
  mood: string | null;
  lifecycle: ChatLifecycle;
};

export type MoodContextValue = MoodState & {
  setMood: (mood: string | null) => void;
  setLifecycle: (lifecycle: ChatLifecycle) => void;
};

const MoodContext = createContext<MoodContextValue | null>(null);

function useMoodStateInternal(): MoodContextValue {
  const [mood, setMood] = useState<string | null>(null);
  const [lifecycle, setLifecycle] = useState<ChatLifecycle>("idle");
  return useMemo(
    () => ({ mood, lifecycle, setMood, setLifecycle }),
    [mood, lifecycle],
  );
}

/** Shares the chat's mood/lifecycle with everything under it (stage, badges).
 * Place it ABOVE both the chat console and whatever should react to it. */
export function MoodProvider({ children }: { children: ReactNode }) {
  const value = useMoodStateInternal();
  return <MoodContext.Provider value={value}>{children}</MoodContext.Provider>;
}

/** The shared mood signal. Requires a `MoodProvider`/`MoodScope` above (throws
 * otherwise — a silent always-null mood would read as "the engine never sends
 * moods"). */
export function useMood(): MoodContextValue {
  const value = useContext(MoodContext);
  if (value === null) {
    throw new Error("useMood needs a <MoodProvider> above it");
  }
  return value;
}

/** Join the ambient `MoodProvider` when one is mounted, else create the state
 * here. The chat console wraps itself in this, so it works standalone AND lifts
 * its signal into a surrounding provider when the page composes one (the
 * companion layout does, so its stage sees the console's stream). */
export function MoodScope({ children }: { children: ReactNode }) {
  const ambient = useContext(MoodContext);
  const local = useMoodStateInternal();
  return (
    <MoodContext.Provider value={ambient ?? local}>{children}</MoodContext.Provider>
  );
}

/** Adapter callbacks bound to a mood context — hand these to
 * `createChatModelAdapter` so the stream drives the shared state. Stable per
 * context identity. */
export function useMoodAdapterEvents(value: MoodContextValue): {
  onMood: (mood: string) => void;
  onLifecycle: (lifecycle: ChatLifecycle) => void;
} {
  const { setMood, setLifecycle } = value;
  const onMood = useCallback((mood: string) => setMood(mood), [setMood]);
  const onLifecycle = useCallback(
    (lifecycle: ChatLifecycle) => setLifecycle(lifecycle),
    [setLifecycle],
  );
  return useMemo(() => ({ onMood, onLifecycle }), [onMood, onLifecycle]);
}
