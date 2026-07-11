"use client";

// ContextInspector — the engine's context accounting, on demand. Where the
// footer's ContextDisplay meter tracks the LAST TURN's reported usage, this
// asks the engine what it would assemble RIGHT NOW (GET /api/chat/context →
// /v1/sessions/{id}/context): per-section token counts (real llama-server
// counts when the deployment can provide them), configured section budgets,
// and the warn threshold — plus the release valve: "fresh session, carry the
// gist" flushes the session server-side (rolling summary → episode, so the
// gist survives into memory) and hands the console a new conversation.
//
// Renders as a small trigger that opens an anchored card; fetches only while
// open (stats are an inspection, not a stream).

import { useCallback, useEffect, useRef, useState } from "react";

import type { ContextStats } from "../lib/chat-api";

const SECTION_LABELS: Record<string, string> = {
  persona: "Persona",
  long_term: "Long-term memory",
  episodes: "Episodes",
  short_term: "This session",
};

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${n}`;
}

export type ContextInspectorProps = {
  sessionId: string;
  userId: string;
  /** Called after a successful flush — the console starts a fresh conversation. */
  onFreshSession?: () => void;
  className?: string;
};

export function ContextInspector({
  sessionId,
  userId,
  onFreshSession,
  className = "",
}: ContextInspectorProps) {
  const [open, setOpen] = useState(false);
  const [stats, setStats] = useState<ContextStats | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "error" | "flushing">("idle");
  const rootRef = useRef<HTMLDivElement | null>(null);

  const refresh = useCallback(() => {
    setState("loading");
    fetch(
      `/api/chat/context?sessionId=${encodeURIComponent(sessionId)}&userId=${encodeURIComponent(userId)}`,
      { cache: "no-store" },
    )
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`${res.status}`))))
      .then((body: ContextStats) => {
        setStats(body);
        setState("idle");
      })
      .catch(() => setState("error"));
  }, [sessionId, userId]);

  useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);

  // Click-away closes the card.
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const flush = () => {
    setState("flushing");
    fetch("/api/chat/flush", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId, userId }),
    })
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`${res.status}`))))
      .then(() => {
        setOpen(false);
        setState("idle");
        onFreshSession?.();
      })
      .catch(() => setState("error"));
  };

  const ratio = stats ? Math.min(stats.ratio, 1) : 0;
  const warnRatio = stats?.warn_ratio ?? 0.75;
  const pressured = stats ? stats.ratio >= warnRatio : false;

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className={`text-ui-2xs font-medium uppercase tracking-wide transition-colors ${
          pressured
            ? "text-amber-500 hover:text-amber-400"
            : "text-[color:var(--ui-ink-subtle)] hover:text-[color:var(--ui-ink)]"
        }`}
        title="Inspect what the assistant's context window holds right now"
        aria-expanded={open}
      >
        Inspect{pressured ? " ⚠" : ""}
      </button>

      {open ? (
        <div className="absolute bottom-full right-0 z-50 mb-2 w-72 rounded-xl border border-ui bg-[color:var(--ui-bg)] p-3 shadow-lg">
          {state === "error" ? (
            <p className="text-ui-2xs text-[color:var(--ui-ink-danger)]">
              Couldn't read the context stats — is the chat API up?
            </p>
          ) : !stats ? (
            <p className="text-ui-2xs text-[color:var(--ui-ink-subtle)]">Reading…</p>
          ) : (
            <div className="flex flex-col gap-2 text-ui-2xs">
              <div className="flex items-center justify-between">
                <span className="font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
                  Context window
                </span>
                <span
                  className="font-mono text-[color:var(--ui-ink-subtle)]"
                  title={
                    stats.token_source === "llamacpp"
                      ? "Counted by the model's own tokenizer"
                      : "Estimated (~4 chars/token)"
                  }
                >
                  {stats.token_source === "llamacpp" ? "exact" : "est."}
                </span>
              </div>

              {/* Fill bar with the warn threshold marked. */}
              <div className="relative h-2 overflow-hidden rounded-full bg-[color:var(--ui-bg-soft)]">
                <div
                  className={`h-full rounded-full transition-all ${
                    pressured ? "bg-amber-500" : "bg-emerald-500"
                  }`}
                  style={{ width: `${ratio * 100}%` }}
                />
                <div
                  className="absolute inset-y-0 w-px bg-[color:var(--ui-ink-subtle)] opacity-60"
                  style={{ left: `${warnRatio * 100}%` }}
                  title={`Warn threshold (${Math.round(warnRatio * 100)}%)`}
                />
              </div>
              <div className="flex items-center justify-between font-mono tabular-nums text-[color:var(--ui-ink-subtle)]">
                <span>
                  {formatTokens(stats.est_tokens)} / {formatTokens(stats.budget_tokens)} tok
                </span>
                <span>{Math.round(stats.ratio * 100)}%</span>
              </div>

              <div className="mt-1 flex flex-col gap-1 border-t border-ui pt-2">
                {Object.entries(stats.sections).map(([name, tokens]) => {
                  const budget = stats.section_budgets?.[name];
                  return (
                    <div key={name} className="flex items-center justify-between gap-3">
                      <span className="text-[color:var(--ui-ink-muted)]">
                        {SECTION_LABELS[name] ?? name}
                      </span>
                      <span className="font-mono tabular-nums text-[color:var(--ui-ink-subtle)]">
                        {formatTokens(tokens)}t
                        {budget ? ` (cap ${formatTokens(Math.ceil(budget / 4))}t)` : ""}
                      </span>
                    </div>
                  );
                })}
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[color:var(--ui-ink-muted)]">Live turns</span>
                  <span className="font-mono tabular-nums text-[color:var(--ui-ink-subtle)]">
                    {stats.short_term_turns}
                  </span>
                </div>
              </div>

              {onFreshSession ? (
                <button
                  type="button"
                  onClick={flush}
                  disabled={state === "flushing"}
                  className="mt-1 rounded-lg border border-ui px-2 py-1.5 text-left font-medium text-[color:var(--ui-ink)] transition-colors hover:border-[color:var(--ui-border-active)] hover:bg-[color:var(--ui-bg-soft)] disabled:opacity-50"
                  title="Close this session server-side (its summary is kept as an episode) and start a new conversation"
                >
                  {state === "flushing" ? "Carrying the gist…" : "Fresh session, carry the gist"}
                </button>
              ) : null}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
