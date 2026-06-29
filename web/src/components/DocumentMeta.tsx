"use client";

// Edit a document's subject (pick from the controlled vocabulary) and its tags
// (free-form, with corpus autocomplete). Calls the BFF doc-subject / doc-tags
// routes and refreshes.

import { useRouter } from "next/navigation";
import { useState } from "react";

export function DocumentMeta({
  docId,
  subject,
  tags,
  allSubjects,
  allTags,
}: {
  docId: string;
  subject: string;
  tags: string[];
  allSubjects: { id: string; name: string }[];
  allTags: string[];
}) {
  const router = useRouter();
  const [tagDraft, setTagDraft] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function setSubject(next: string) {
    setError(null);
    const res = await fetch("/api/admin/knowledge/doc-subject", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ docId, subject: next }),
    });
    if (res.ok) router.refresh();
    else setError(`Subject change failed (${res.status}).`);
  }

  async function changeTags(change: { add?: string[]; remove?: string[] }) {
    setError(null);
    const res = await fetch("/api/admin/knowledge/doc-tags", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ docId, ...change }),
    });
    if (res.ok) {
      setTagDraft("");
      router.refresh();
    } else {
      setError(`Tag change failed (${res.status}).`);
    }
  }

  return (
    <div style={{ display: "flex", gap: "1.5rem", flexWrap: "wrap", margin: "0.75rem 0" }}>
      <label style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
        <span className="muted">Subject</span>
        <select
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          style={{
            padding: "0.35rem 0.5rem",
            background: "#15171b",
            color: "var(--fg)",
            border: "1px solid var(--border)",
            borderRadius: 6,
          }}
        >
          <option value="">— none —</option>
          {allSubjects.map((s) => (
            <option key={s.id} value={s.name}>
              {s.name}
            </option>
          ))}
        </select>
      </label>

      <div style={{ display: "flex", gap: "0.4rem", alignItems: "center", flexWrap: "wrap" }}>
        <span className="muted">Tags</span>
        {tags.map((t) => (
          <span
            key={t}
            style={{
              border: "1px solid var(--border)",
              borderRadius: 12,
              padding: "0.1rem 0.5rem",
              fontSize: "0.85rem",
            }}
          >
            {t}{" "}
            <button
              className="ghost"
              style={{ padding: "0 0.25rem", border: 0 }}
              onClick={() => changeTags({ remove: [t] })}
              aria-label={`Remove ${t}`}
            >
              ×
            </button>
          </span>
        ))}
        <input
          aria-label="Add tag"
          list="tag-options"
          placeholder="add tag…"
          value={tagDraft}
          onChange={(e) => setTagDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && tagDraft.trim()) {
              e.preventDefault();
              changeTags({ add: [tagDraft.trim()] });
            }
          }}
          style={{
            padding: "0.3rem 0.5rem",
            background: "#15171b",
            color: "var(--fg)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            width: 120,
          }}
        />
        <datalist id="tag-options">
          {allTags.map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
      </div>
      {error ? <span className="error">{error}</span> : null}
    </div>
  );
}
