"use client";

// Rename + delete controls for a knowledge document. Client component: calls the
// BFF mutation routes, then refreshes (rename) or navigates back (delete).

import { useRouter } from "next/navigation";
import { useState } from "react";

import { encodeDocId } from "@/lib/encode";
import type { FormEvent } from "react";

export function DocumentActions({ docId, title }: { docId: string; title: string }) {
  const router = useRouter();
  const [value, setValue] = useState(title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const base = `/api/admin/knowledge/documents/${encodeDocId(docId)}`;

  async function rename(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const res = await fetch(base, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: value }),
    });
    setBusy(false);
    if (res.ok) router.refresh();
    else setError(`Rename failed (${res.status}).`);
  }

  async function remove() {
    if (!confirm(`Delete "${title}"? This removes all its chunks.`)) return;
    setBusy(true);
    setError(null);
    const res = await fetch(base, { method: "DELETE" });
    if (res.ok) {
      router.push("/knowledge");
      return;
    }
    setBusy(false);
    setError(`Delete failed (${res.status}).`);
  }

  return (
    <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap" }}>
      <form onSubmit={rename} style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
        <input
          aria-label="Title"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          style={{
            padding: "0.4rem 0.6rem",
            background: "#15171b",
            color: "var(--fg)",
            border: "1px solid var(--border)",
            borderRadius: 6,
          }}
        />
        <button type="submit" disabled={busy || !value.trim() || value === title}>
          Rename
        </button>
      </form>
      <button className="ghost" onClick={remove} disabled={busy}>
        Delete
      </button>
      {error ? <span className="error">{error}</span> : null}
    </div>
  );
}
