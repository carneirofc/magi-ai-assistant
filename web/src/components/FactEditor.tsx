"use client";

// Per-fact CRUD on a user's curated profile. Holds the optimistic-concurrency
// version locally and advances it from each write's response, so consecutive edits
// don't 409 against themselves; a real conflict (the curator wrote meanwhile)
// surfaces as a 409 prompt to reload.

import { useState } from "react";

type Fact = { id: string; text: string; ts: string };

export function FactEditor({
  userId,
  initialFacts,
  initialVersion,
}: {
  userId: string;
  initialFacts: Fact[];
  initialVersion: string;
}) {
  const [facts, setFacts] = useState(initialFacts);
  const [version, setVersion] = useState(initialVersion);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function send(method: string, payload: object): Promise<boolean> {
    setError(null);
    const res = await fetch("/api/admin/facts", {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ userId, expectedVersion: version, ...payload }),
    });
    if (res.ok) {
      const data = (await res.json()) as { facts: Fact[]; version: string };
      setFacts(data.facts);
      setVersion(data.version);
      return true;
    }
    setError(
      res.status === 409
        ? "Changed elsewhere since you loaded — reload the page."
        : `Failed (${res.status}).`,
    );
    return false;
  }

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (await send("POST", { text: draft })) setDraft("");
  }

  async function edit(f: Fact) {
    const next = prompt("Edit fact:", f.text);
    if (next && next !== f.text) await send("PATCH", { factId: f.id, text: next });
  }

  async function remove(f: Fact) {
    if (confirm(`Delete fact "${f.text}"?`)) await send("DELETE", { factId: f.id });
  }

  return (
    <section>
      <h2>Profile facts</h2>
      {facts.length === 0 ? (
        <p className="muted">No curated facts.</p>
      ) : (
        <ul>
          {facts.map((f) => (
            <li key={f.id} style={{ marginBottom: "0.3rem" }}>
              {f.text}{" "}
              <button className="ghost" onClick={() => edit(f)}>
                Edit
              </button>{" "}
              <button className="ghost" onClick={() => remove(f)}>
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={add} style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}>
        <input
          aria-label="New fact"
          placeholder="Add a fact…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          style={{
            flex: 1,
            padding: "0.45rem 0.7rem",
            background: "#15171b",
            color: "var(--fg)",
            border: "1px solid var(--border)",
            borderRadius: 6,
          }}
        />
        <button type="submit" disabled={!draft.trim()}>
          Add
        </button>
      </form>
      {error ? <p className="error">{error}</p> : null}
    </section>
  );
}
