"use client";

// Edit one memory file as raw text (persona, episodes, a session's window/summary/
// pending). Saves via the BFF raw-file route with the optimistic-concurrency
// version; relays 409 (changed elsewhere) and 422 (invalid JSON shape).

import { useState } from "react";
import { OutlineButton, StatusMessage, TextAreaInput } from "@carneirofc/ui";

export function RawFileEditor({
  kind,
  label,
  description,
  userId,
  sessionId,
  initialContent,
  initialVersion,
}: {
  kind: string;
  label: string;
  description?: string;
  userId?: string;
  sessionId?: string;
  initialContent: string;
  initialVersion: string;
}) {
  const [content, setContent] = useState(initialContent);
  const [version, setVersion] = useState(initialVersion);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    </div>
  );
}
