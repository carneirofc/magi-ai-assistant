"use client";

// One session file with two views: a Rendered transcript (chat bubbles for the
// turn lists, prose for the summary) and a Raw editor. Both share the same content
// state so an edit in Raw is reflected in Rendered without a reload. Saves via the
// BFF raw-file route with optimistic-concurrency; relays 409 / 422.

import { useMemo, useState } from "react";
import {
  OutlineButton,
  SegmentedControl,
  StatusMessage,
  TextAreaInput,
} from "@carneirofc/ui";

type Turn = { role?: string; content?: string; ts?: string };
type ViewMode = "rendered" | "raw";

export function SessionFile({
  kind,
  label,
  description,
  userId,
  sessionId,
  initialContent,
  initialVersion,
  render,
}: {
  kind: string;
  label: string;
  description?: string;
  userId: string;
  sessionId: string;
  initialContent: string;
  initialVersion: string;
  /** "turns" parses JSON turn lists into a transcript; "text" shows prose. */
  render: "turns" | "text";
}) {
  const [content, setContent] = useState(initialContent);
  const [version, setVersion] = useState(initialVersion);
  const [view, setView] = useState<ViewMode>("rendered");
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const turns = useMemo<Turn[] | null>(() => {
    if (render !== "turns") return null;
    try {
      const parsed = JSON.parse(content || "[]");
      return Array.isArray(parsed) ? (parsed as Turn[]) : null;
    } catch {
      return null;
    }
  }, [content, render]);

  async function save() {
    setError(null);
    setSaved(false);
    setBusy(true);
    const res = await fetch("/api/admin/raw-file", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, userId, sessionId, content, expectedVersion: version }),
    });
    setBusy(false);
    if (res.ok) {
      const data = (await res.json()) as { version: string };
      setVersion(data.version);
      setSaved(true);
      setDirty(false);
      return;
    }
    if (res.status === 409) setError("Changed elsewhere since you loaded — reload.");
    else if (res.status === 422) setError("Invalid content (JSON files must be a list).");
    else setError(`Save failed (${res.status}).`);
  }

  const rows = Math.min(24, Math.max(4, content.split("\n").length + 1));

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-ui-md font-semibold">{label}</h2>
          {description ? (
            <p className="text-ui-xs text-[color:var(--ui-ink-subtle)]">{description}</p>
          ) : null}
        </div>
        <SegmentedControl<ViewMode>
          value={view}
          onValueChange={setView}
          options={[
            { value: "rendered", label: "Rendered" },
            { value: "raw", label: "Raw" },
          ]}
        />
      </div>

      {view === "rendered" ? (
        render === "turns" ? (
          turns === null ? (
            <StatusMessage role="status" tone="warn">
              Not valid turn JSON — switch to Raw to inspect.
            </StatusMessage>
          ) : turns.length === 0 ? (
            <p className="text-ui-sm text-[color:var(--ui-ink-subtle)]">No turns.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {turns.map((t, i) => (
                <TurnBubble key={i} turn={t} />
              ))}
            </ul>
          )
        ) : content.trim() ? (
          <div className="whitespace-pre-wrap rounded-lg bg-[color:var(--ui-bg-soft)] p-3 text-ui-sm text-[color:var(--ui-ink-muted)]">
            {content}
          </div>
        ) : (
          <p className="text-ui-sm text-[color:var(--ui-ink-subtle)]">Empty.</p>
        )
      ) : (
        <>
          <TextAreaInput
            value={content}
            onChange={(e) => {
              setContent(e.target.value);
              setDirty(true);
              setSaved(false);
            }}
            rows={rows}
            spellCheck={false}
            className="font-mono text-ui-xs"
          />
          <div className="flex items-center gap-3">
            <OutlineButton variant="accent" controlSize="md" onClick={save} disabled={busy || !dirty}>
              {busy ? "Saving…" : "Save"}
            </OutlineButton>
            {saved ? (
              <span className="text-ui-xs text-[color:var(--status-success-text)]">Saved.</span>
            ) : null}
          </div>
        </>
      )}

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : null}
    </div>
  );
}

const ROLE_STYLE: Record<string, string> = {
  user: "border-accent-cyan/40 bg-[color:var(--ui-bg-info)]",
  assistant: "border-ui bg-[color:var(--ui-bg-soft)]",
  system: "border-ui-soft bg-[color:var(--ui-bg-muted)]",
};

function TurnBubble({ turn }: { turn: Turn }) {
  const role = (turn.role || "unknown").toLowerCase();
  const tone = ROLE_STYLE[role] ?? "border-ui bg-[color:var(--ui-bg-soft)]";
  return (
    <li className={`rounded-lg border px-3 py-2 ${tone}`}>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-accent)]">
          {turn.role || "unknown"}
        </span>
        {turn.ts ? (
          <span className="text-ui-2xs text-[color:var(--ui-ink-subtle)]">{turn.ts}</span>
        ) : null}
      </div>
      <div className="whitespace-pre-wrap text-ui-sm text-[color:var(--ui-ink)]">
        {turn.content ?? ""}
      </div>
    </li>
  );
}
