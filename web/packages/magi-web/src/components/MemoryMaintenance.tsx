"use client";

// MemoryMaintenance — the operator's depth tools for one user's durable memory:
//
//   - Recall preview: dry-run the context assembly for a query and see exactly
//     what each memory section would inject (with semantic memory on, this is
//     THE lens for judging retrieval quality — which facts/episodes a phrasing
//     actually surfaces).
//   - Consolidate: maintenance curation over the whole fact sheet — merge
//     duplicates, drop contradictions/stale facts. The per-turn curator only
//     touches what a turn changed, so sheets drift toward near-duplicates;
//     this is the cleanup lever. 503 = no model wired, surfaced as a note.

import { useState } from "react";
import { useRouter } from "next/navigation";
import { OutlineButton, StatusMessage, TextInput } from "@carneirofc/ui";

const SECTION_LABELS: Record<string, string> = {
  persona: "Persona",
  long_term: "Long-term memory",
  episodes: "Episodes",
  short_term: "Short-term (empty in preview)",
};

type Preview = { query: string; sections: Record<string, string> };
type Tone = "success" | "warn" | "error";

export function MemoryMaintenance({ userId }: { userId: string }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState<"preview" | "consolidate" | null>(null);
  const [status, setStatus] = useState<{ tone: Tone; text: string } | null>(null);

  async function runPreview() {
    const q = query.trim();
    if (!q) return;
    setBusy("preview");
    setStatus(null);
    try {
      const res = await fetch(
        `/api/admin/memory/recall-preview?userId=${encodeURIComponent(userId)}&q=${encodeURIComponent(q)}`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error(`${res.status}`);
      setPreview((await res.json()) as Preview);
    } catch {
      setStatus({ tone: "error", text: "Preview failed — is the admin API up?" });
    } finally {
      setBusy(null);
    }
  }

  async function consolidate() {
    setBusy("consolidate");
    setStatus(null);
    try {
      const res = await fetch("/api/admin/memory/consolidate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userId }),
      });
      if (res.status === 503) {
        setStatus({ tone: "warn", text: "Unavailable — no model wired for curation here." });
        return;
      }
      if (!res.ok) throw new Error(`${res.status}`);
      const data = (await res.json()) as { changed: boolean; detail: string };
      setStatus({ tone: data.changed ? "success" : "warn", text: data.detail });
      if (data.changed) router.refresh();
    } catch {
      setStatus({ tone: "error", text: "Consolidation failed — is the admin API up?" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <h2 className="text-ui-md font-semibold">Recall & maintenance</h2>
          <p className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
            Preview what a query would pull into context, and clean up the fact sheet.
          </p>
        </div>
        <OutlineButton
          controlSize="sm"
          onClick={consolidate}
          disabled={busy !== null}
          title="Merge duplicate facts and drop contradictions (one model pass)"
        >
          {busy === "consolidate" ? "Consolidating…" : "Consolidate facts"}
        </OutlineButton>
      </div>

      <form
        className="flex items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void runPreview();
        }}
      >
        <TextInput
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="What would she recall for… e.g. 'docker build'"
          className="flex-1 text-ui-xs"
          aria-label="Recall preview query"
        />
        <OutlineButton controlSize="sm" type="submit" disabled={busy !== null || !query.trim()}>
          {busy === "preview" ? "Previewing…" : "Preview recall"}
        </OutlineButton>
      </form>

      {status ? (
        <StatusMessage role="status" tone={status.tone}>
          {status.text}
        </StatusMessage>
      ) : null}

      {preview ? (
        <div className="flex flex-col gap-2">
          {Object.entries(preview.sections).map(([name, body]) => (
            <details
              key={name}
              open={name === "long_term" || name === "episodes"}
              className="rounded-lg border border-ui bg-[color:var(--ui-bg)] px-3 py-2"
            >
              <summary className="cursor-pointer text-ui-xs font-medium text-[color:var(--ui-ink-muted)]">
                {SECTION_LABELS[name] ?? name}
                <span className="ml-2 font-mono text-[10px] text-[color:var(--ui-ink-subtle)]">
                  {body ? `${body.length} chars` : "empty"}
                </span>
              </summary>
              <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap text-ui-2xs text-[color:var(--ui-ink)]">
                {body || "(nothing would be injected)"}
              </pre>
            </details>
          ))}
        </div>
      ) : null}
    </div>
  );
}
