"use client";

// CompanionSurface — the companion chat arrangement: the persona visually
// present beside the conversation, reacting to the stream.
//
// Wide viewports get a side stage (portrait column, full-height transcript
// beside it); narrow ones collapse the stage to a compact header bust so the
// face stays visible without eating the transcript. One MoodProvider spans the
// whole surface, so the stage, the bust, and the chat console (whose MoodScope
// joins an ambient provider) all see the same signal.
//
// Composition-first: the app owns the route and passes its chat surface as
// children (usually <ChatConsole/>) plus the persona's expression map; `aside`
// mounts extra stage-column content (a memory panel, status pills) under the
// portrait.

import type { ReactNode } from "react";

import { MoodProvider, useMood } from "../lib/chat-mood";
import { VoiceProvider } from "../lib/chat-voice";
import { PersonaStage, resolveExpression } from "./PersonaStage";

export type CompanionSurfaceProps = {
  /** mood name → portrait URL (see PersonaStage). */
  expressions: Record<string, string>;
  /** The persona's display name (bust label, alt text, placeholder monogram). */
  name?: string | null;
  /** Extra stage-column content under the portrait (memory panel, pills). */
  aside?: ReactNode;
  /** The chat surface, usually <ChatConsole/>. */
  children: ReactNode;
  className?: string;
};

/** The narrow-viewport face: a small round portrait + name + live mood, laid
 * out as a header row above the transcript. */
function CompanionBust({
  expressions,
  name,
}: {
  expressions: Record<string, string>;
  name: string | null;
}) {
  const { mood, lifecycle } = useMood();
  const src = resolveExpression(expressions, mood, "neutral");
  return (
    <div className="flex items-center gap-3 rounded-xl border border-ui bg-[color:var(--ui-bg)] px-3 py-2">
      {src ? (
        <img
          src={src}
          alt={name ?? "assistant portrait"}
          draggable={false}
          className="h-10 w-10 select-none rounded-full border border-ui object-cover"
        />
      ) : (
        <div className="flex h-10 w-10 items-center justify-center rounded-full border border-ui bg-[color:var(--ui-bg-soft)] text-ui-sm font-semibold text-[color:var(--ui-ink-subtle)]">
          {(name ?? "?").slice(0, 1).toUpperCase()}
        </div>
      )}
      <div className="flex min-w-0 flex-col">
        {name ? (
          <span className="truncate text-ui-sm font-medium text-[color:var(--ui-ink)]">
            {name}
          </span>
        ) : null}
        <span className="flex items-center gap-1.5 text-ui-2xs text-[color:var(--ui-ink-subtle)]">
          {mood ? (
            <span className="font-mono text-[color:var(--ui-ink-accent)]">{mood}</span>
          ) : null}
          {lifecycle !== "idle" ? <span>· {lifecycle}</span> : null}
        </span>
      </div>
    </div>
  );
}

export function CompanionSurface({
  expressions,
  name = null,
  aside,
  children,
  className = "",
}: CompanionSurfaceProps) {
  return (
    <MoodProvider>
    <VoiceProvider>
      <div className={`flex min-h-0 flex-1 flex-col gap-3 lg:flex-row ${className}`}>
        {/* Narrow: the face rides a compact header bust above the transcript. */}
        <div className="lg:hidden">
          <CompanionBust expressions={expressions} name={name} />
        </div>

        {/* Wide: the side stage — portrait + status + whatever the app mounts
            under it — while the transcript keeps its full height beside it. */}
        <aside className="hidden w-60 shrink-0 flex-col gap-3 lg:flex xl:w-72">
          <PersonaStage expressions={expressions} name={name} />
          {aside}
        </aside>

        <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>
      </div>
    </VoiceProvider>
    </MoodProvider>
  );
}
