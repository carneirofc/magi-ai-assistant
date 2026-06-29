"use client";

// Edit one memory file as raw text (persona, episodes, a session's window/summary/
// pending). Saves via the BFF raw-file route with the optimistic-concurrency
// version; relays 409 (changed elsewhere) and 422 (invalid JSON shape).

import { useState } from "react";

export function RawFileEditor({
  kind,
  label,
  userId,
  sessionId,
  initialContent,
  initialVersion,
}: {
  kind: string;
  label: string;
  userId?: string;
  sessionId?: string;
  initialContent: string;
  initialVersion: string;
}) {
  const [content, setContent] = useState(initialContent);
  const [version, setVersion] = useState(initialVersion);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    setSaved(false);
    const res = await fetch("/api/admin/raw-file", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, userId, sessionId, content, expectedVersion: version }),
    });
    if (res.ok) {
      const data = (await res.json()) as { version: string };
      setVersion(data.version);
      setSaved(true);
      return;
    }
    if (res.status === 409) setError("Changed elsewhere since you loaded — reload.");
    else if (res.status === 422) setError("Invalid content (JSON files must be a list).");
    else setError(`Save failed (${res.status}).`);
  }

  return (
    <section>
      <h2>{label}</h2>
      <textarea
        value={content}
        onChange={(e) => {
          setContent(e.target.value);
          setSaved(false);
        }}
        rows={Math.min(20, Math.max(4, content.split("\n").length + 1))}
        spellCheck={false}
        style={{
          width: "100%",
          fontFamily: "ui-monospace, monospace",
          fontSize: "0.85rem",
          background: "#15171b",
          color: "var(--fg)",
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: "0.6rem",
        }}
      />
      <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", marginTop: "0.4rem" }}>
        <button onClick={save}>Save</button>
        {saved ? <span className="muted">Saved.</span> : null}
        {error ? <span className="error">{error}</span> : null}
      </div>
    </section>
  );
}
