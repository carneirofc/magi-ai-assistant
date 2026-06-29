"use client";

// Create / rename / delete subjects (the controlled vocabulary). Calls the BFF
// subject routes and refreshes the server-rendered list.

import { useRouter } from "next/navigation";
import { useState } from "react";

type Subject = { id: string; name: string; description: string };

export function SubjectManager({ subjects }: { subjects: Subject[] }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const res = await fetch("/api/admin/subjects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (res.ok) {
      setName("");
      router.refresh();
    } else {
      setError(res.status === 409 ? "A subject with that name exists." : `Failed (${res.status}).`);
    }
  }

  async function rename(id: string, current: string) {
    const next = prompt("New subject name:", current);
    if (!next || next === current) return;
    const res = await fetch(`/api/admin/subjects/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: next }),
    });
    if (res.ok) router.refresh();
  }

  async function remove(id: string, current: string) {
    if (!confirm(`Delete subject "${current}"? Documents keep their label.`)) return;
    const res = await fetch(`/api/admin/subjects/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (res.ok) router.refresh();
  }

  return (
    <>
      <form onSubmit={create} style={{ display: "flex", gap: "0.5rem", margin: "1rem 0" }}>
        <input
          aria-label="New subject"
          placeholder="New subject name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{
            padding: "0.45rem 0.7rem",
            background: "#15171b",
            color: "var(--fg)",
            border: "1px solid var(--border)",
            borderRadius: 6,
          }}
        />
        <button type="submit" disabled={!name.trim()}>
          Add subject
        </button>
        {error ? <span className="error">{error}</span> : null}
      </form>

      {subjects.length === 0 ? (
        <p className="muted">No subjects yet.</p>
      ) : (
        <ul>
          {subjects.map((s) => (
            <li key={s.id} style={{ marginBottom: "0.4rem" }}>
              {s.name}{" "}
              <button className="ghost" onClick={() => rename(s.id, s.name)}>
                Rename
              </button>{" "}
              <button className="ghost" onClick={() => remove(s.id, s.name)}>
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
