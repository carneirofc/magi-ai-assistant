"use client";

// MemoryPanel — what the assistant currently remembers about the user, as a
// compact card designed to sit under the companion stage. Ambient transparency:
// the user can always see the durable facts accruing to them (read-only here;
// edits live on the admin Memory pages).
//
// Refresh model: fetch on mount, then again each time a turn completes — it
// reads the ambient mood context's lifecycle (tool/streaming → idle), the same
// signal the stage animates, so a turn that changed durable memory shows up
// without polling. Requires a MoodProvider above (the companion surface has
// one); the facts come from the app's BFF relay at /api/memory.

import { useEffect, useRef, useState } from "react";

import type { SelfMemoryFact } from "../lib/chat-api";
import { useMood } from "../lib/chat-mood";

export function MemoryPanel({
  userId,
  title = "What she remembers",
  className = "",
}: {
  /** Whose memory to show — the companion passes its pinned user id. */
  userId: string;
  title?: string;
  className?: string;
}) {
  const { lifecycle } = useMood();
  const [facts, setFacts] = useState<SelfMemoryFact[] | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  // Refetch on mount, on a user switch, and on every turn completion (any
  // lifecycle edge back to idle — see the module docstring).
  const wasBusy = useRef(false);
  const busy = lifecycle === "thinking" || lifecycle === "streaming" || lifecycle === "tool";
  const settled = wasBusy.current && !busy;
  wasBusy.current = busy;

  useEffect(() => {
    if (busy) return; // fetch only at rest; `settled` retriggers this effect
    let active = true;
    fetch(`/api/memory?user_id=${encodeURIComponent(userId)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { facts?: SelfMemoryFact[] } | null) => {
        if (!active) return;
        if (!body || !Array.isArray(body.facts)) {
          setUnavailable(true);
          return;
        }
        setUnavailable(false);
        setFacts(body.facts);
      })
      .catch(() => {
        if (active) setUnavailable(true);
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `settled` is the refresh edge
  }, [userId, busy, settled]);

  // Unreachable backend (or an engine predating the endpoint): stay quiet
  // rather than decorating the stage with an error.
  if (unavailable) return null;

  return (
    <section
      className={`flex flex-col gap-2 rounded-xl border border-ui bg-[color:var(--ui-bg)] p-3 ${className}`}
      aria-label={title}
    >
      <span className="text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
        {title}
      </span>
      {facts === null ? (
        <span className="text-ui-xs text-[color:var(--ui-ink-subtle)]">…</span>
      ) : facts.length === 0 ? (
        <span className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
          Nothing yet — she learns as you talk.
        </span>
      ) : (
        <ul className="flex max-h-56 flex-col gap-1.5 overflow-y-auto">
          {facts.map((fact, i) => (
            <li
              key={`${fact.ts}-${i}`}
              className="text-ui-xs leading-snug text-[color:var(--ui-ink-muted)]"
              title={fact.ts || undefined}
            >
              {fact.text}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
