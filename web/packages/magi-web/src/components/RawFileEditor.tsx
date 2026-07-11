"use client";

// Edit one memory file as raw text (persona, episodes, a session's window/summary/
// pending). Saves via the BFF raw-file route with the optimistic-concurrency
// version; relays 409 (changed elsewhere) and 422 (invalid JSON shape).
//
// When the memory tree is git-versioned (memory_git_enabled), a History drawer
// lists this file's commits; a version can be viewed and loaded into the editor
// (restoring = loading + Save, so every restore is itself a versioned write).
// With versioning off the drawer quietly hides — empty history is not an error.

import { useEffect, useState } from "react";
import { OutlineButton, StatusMessage, TextAreaInput } from "@carneirofc/ui";

type HistoryEntry = { sha: string; ts: string; message: string };

export function RawFileEditor({
  kind,
  label,
  description,
  userId,
  sessionId,
  initialContent,
  initialVersion,
  maxRows = 24,
}: {
  kind: string;
  label: string;
  description?: string;
  userId?: string;
  sessionId?: string;
  initialContent: string;
  initialVersion: string;
  maxRows?: number;
}) {
  const [content, setContent] = useState(initialContent);
  const [version, setVersion] = useState(initialVersion);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Git history (null until fetched; [] = versioning off → drawer hidden).
  const [history, setHistory] = useState<HistoryEntry[] | null>(null);
  const [viewing, setViewing] = useState<{ sha: string; content: string } | null>(null);

  const historyQuery = () => {
    const p = new URLSearchParams({ kind });
    if (userId) p.set("userId", userId);
    if (sessionId) p.set("sessionId", sessionId);
    return p.toString();
  };

  useEffect(() => {
    let active = true;
    fetch(`/api/admin/memory/file-history?${historyQuery()}`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { entries?: HistoryEntry[] } | null) => {
        if (active) setHistory(body && Array.isArray(body.entries) ? body.entries : []);
      })
      .catch(() => {
        if (active) setHistory([]);
      });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- identity is (kind,userId,sessionId)
  }, [kind, userId, sessionId]);

  async function viewVersion(sha: string) {
    setError(null);
    const res = await fetch(`/api/admin/memory/file-history?${historyQuery()}&sha=${sha}`, {
      cache: "no-store",
    });
    if (!res.ok) {
      setError(`Couldn't read that version (${res.status}).`);
      return;
    }
    const body = (await res.json()) as { content?: string };
    setViewing({ sha, content: body.content ?? "" });
  }

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

  const rows = Math.min(maxRows, Math.max(4, content.split("\n").length + 1));

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <h2 className="text-ui-md font-semibold">{label}</h2>
          {description ? (
            <p className="text-ui-xs text-[color:var(--ui-ink-subtle)]">{description}</p>
          ) : null}
        </div>
      </div>
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
      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : null}

      {history && history.length > 0 ? (
        <details className="rounded-lg border border-ui bg-[color:var(--ui-bg)] px-3 py-2">
          <summary className="cursor-pointer text-ui-xs font-medium text-[color:var(--ui-ink-muted)]">
            History
            <span className="ml-2 font-mono text-[10px] text-[color:var(--ui-ink-subtle)]">
              {history.length} version{history.length === 1 ? "" : "s"}
            </span>
          </summary>
          <ul className="mt-2 flex max-h-40 flex-col gap-1 overflow-y-auto">
            {history.map((entry) => (
              <li key={entry.sha} className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void viewVersion(entry.sha)}
                  className={`flex min-w-0 flex-1 items-baseline gap-2 rounded px-1.5 py-1 text-left hover:bg-[color:var(--ui-bg-soft)] ${
                    viewing?.sha === entry.sha ? "bg-[color:var(--ui-bg-soft)]" : ""
                  }`}
                  title="View this version"
                >
                  <span className="font-mono text-[10px] text-[color:var(--ui-ink-accent)]">
                    {entry.sha.slice(0, 7)}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-[color:var(--ui-ink-subtle)]">
                    {entry.ts.slice(0, 16).replace("T", " ")}
                  </span>
                  <span className="truncate text-ui-2xs text-[color:var(--ui-ink-muted)]">
                    {entry.message}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {viewing ? (
            <div className="mt-2 flex flex-col gap-2 border-t border-ui pt-2">
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-ui-2xs text-[color:var(--ui-ink)]">
                {viewing.content || "(file was empty at this version)"}
              </pre>
              <div className="flex items-center gap-2">
                <OutlineButton
                  controlSize="sm"
                  onClick={() => {
                    setContent(viewing.content);
                    setDirty(true);
                    setSaved(false);
                  }}
                  title="Copy this version into the editor — Save then makes the restore a new versioned write"
                >
                  Load into editor
                </OutlineButton>
                <OutlineButton controlSize="sm" onClick={() => setViewing(null)}>
                  Close
                </OutlineButton>
              </div>
            </div>
          ) : null}
        </details>
      ) : null}
    </div>
  );
}
