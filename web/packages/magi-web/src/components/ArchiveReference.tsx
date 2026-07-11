"use client";

// ArchiveReference — cite a previous conversation in the next message. A
// composer-toolbar button opens a small search card over the ENGINE's history
// archive (/api/chat/archive → /v1/sessions/search: old transcripts, session
// summaries, episodes — the server's truth, reboot-proof); picking a hit folds
// it into the composer as a Markdown blockquote with its provenance, so the
// assistant sees exactly what's being referred back to.
//
// Must live inside the assistant-ui runtime (it writes through
// useComposerRuntime); the Composer toolbar mounts it.

import { useEffect, useRef, useState } from "react";
import { useComposerRuntime } from "@assistant-ui/react";

import type { ArchiveHit } from "../lib/chat-api";

const KIND_LABEL: Record<string, string> = {
  transcript: "said in",
  summary: "summary of",
  episode: "episode",
};

export function ArchiveReference({ userId }: { userId: string }) {
  const composer = useComposerRuntime();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<ArchiveHit[] | null>(null);
  const [failed, setFailed] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Debounced search while the card is open.
  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    if (q.length < 2) {
      setHits(null);
      setFailed(false);
      return;
    }
    const timer = setTimeout(() => {
      fetch(
        `/api/chat/archive?q=${encodeURIComponent(q)}&userId=${encodeURIComponent(userId)}`,
        { cache: "no-store" },
      )
        .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`${res.status}`))))
        .then((body: { hits?: ArchiveHit[] }) => {
          setHits(Array.isArray(body.hits) ? body.hits : []);
          setFailed(false);
        })
        .catch(() => setFailed(true));
    }, 250);
    return () => clearTimeout(timer);
  }, [open, query, userId]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

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

  const insert = (hit: ArchiveHit) => {
    const where =
      hit.kind === "episode"
        ? "from a past conversation"
        : `from a previous conversation (${KIND_LABEL[hit.kind] ?? "in"} session ${hit.session_id})`;
    const quote = hit.snippet
      .split("\n")
      .map((line) => `> ${line}`)
      .join("\n");
    const block = `${quote}\n> — ${where}\n\n`;
    const existing = composer.getState().text;
    composer.setText(existing ? `${block}${existing}` : block);
    setOpen(false);
    setQuery("");
    setHits(null);
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-transparent text-[color:var(--ui-ink-muted)] transition-colors hover:border-ui hover:bg-[color:var(--ui-bg-soft)] hover:text-[color:var(--ui-ink)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--ui-border-active)]"
        title="Reference a previous conversation"
        aria-label="Reference a previous conversation"
        aria-expanded={open}
      >
        <HistoryIcon />
      </button>

      {open ? (
        <div className="absolute bottom-full left-0 z-50 mb-2 w-80 rounded-xl border border-ui bg-[color:var(--ui-bg)] p-2 shadow-lg">
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search past conversations…"
            className="w-full rounded-lg border border-ui bg-transparent px-2 py-1.5 text-ui-xs text-[color:var(--ui-ink)] outline-none placeholder:text-[color:var(--ui-ink-subtle)] focus:border-[color:var(--ui-border-active)]"
            aria-label="Search past conversations"
          />
          <div className="mt-1.5 flex max-h-56 flex-col gap-1 overflow-y-auto">
            {failed ? (
              <p className="px-1 py-2 text-ui-2xs text-[color:var(--ui-ink-danger)]">
                Search failed — is the chat API up?
              </p>
            ) : hits === null ? (
              <p className="px-1 py-2 text-ui-2xs text-[color:var(--ui-ink-subtle)]">
                Type to search what was said in earlier sessions.
              </p>
            ) : hits.length === 0 ? (
              <p className="px-1 py-2 text-ui-2xs text-[color:var(--ui-ink-subtle)]">
                Nothing matching in past conversations.
              </p>
            ) : (
              hits.map((hit, index) => (
                <button
                  key={`${hit.kind}-${hit.session_id}-${index}`}
                  type="button"
                  onClick={() => insert(hit)}
                  className="rounded-lg border border-transparent px-2 py-1.5 text-left transition-colors hover:border-ui hover:bg-[color:var(--ui-bg-soft)]"
                  title="Insert as a quote"
                >
                  <span className="block truncate text-ui-2xs text-[color:var(--ui-ink)]">
                    {hit.role ? <span className="opacity-60">{hit.role}: </span> : null}
                    {hit.snippet}
                  </span>
                  <span className="mt-0.5 block font-mono text-[10px] text-[color:var(--ui-ink-subtle)]">
                    {hit.kind}
                    {hit.session_id ? ` · ${hit.session_id}` : ""}
                    {hit.ts ? ` · ${hit.ts.slice(0, 10)}` : ""}
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function HistoryIcon() {
  return (
    <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
      <path d="M3 3v5h5" />
      <path d="M12 7v5l3.5 2" />
    </svg>
  );
}
