"use client";

// The knowledge document table with filters: subject is a hard filter (a row must
// match), tags are soft (matching rows float to the top but none are hidden) —
// mirroring how the model's search_knowledge treats them.

import Link from "next/link";
import { useMemo, useState } from "react";

import { encodeDocId } from "@/lib/encode";

type Doc = {
  doc_id: string;
  source: string;
  title: string;
  subject: string;
  tags: string[];
  chunk_count: number;
  latest_ts: string;
};

export function KnowledgeList({
  documents,
  subjects,
}: {
  documents: Doc[];
  subjects: string[];
}) {
  const [subject, setSubject] = useState("");
  const [tag, setTag] = useState("");

  const rows = useMemo(() => {
    const filtered = subject ? documents.filter((d) => d.subject === subject) : [...documents];
    if (tag.trim()) {
      const t = tag.trim().toLowerCase();
      // Soft: matches sort first, others remain.
      filtered.sort((a, b) => {
        const am = a.tags.some((x) => x.toLowerCase().includes(t)) ? 0 : 1;
        const bm = b.tags.some((x) => x.toLowerCase().includes(t)) ? 0 : 1;
        return am - bm;
      });
    }
    return filtered;
  }, [documents, subject, tag]);

  return (
    <>
      <div style={{ display: "flex", gap: "1rem", margin: "0.75rem 0" }}>
        <label style={{ display: "flex", gap: "0.4rem", alignItems: "center" }}>
          <span className="muted">Subject</span>
          <select value={subject} onChange={(e) => setSubject(e.target.value)}>
            <option value="">all</option>
            {subjects.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <input
          aria-label="Filter by tag"
          placeholder="tag bias…"
          value={tag}
          onChange={(e) => setTag(e.target.value)}
        />
      </div>

      {rows.length === 0 ? (
        <p className="muted">No documents match.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Document</th>
              <th>Subject</th>
              <th>Tags</th>
              <th>Chunks</th>
              <th>Last ingested</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.doc_id}>
                <td>
                  <Link href={`/knowledge/${encodeDocId(d.doc_id)}`}>
                    {d.title || d.source || d.doc_id}
                  </Link>
                  <div className="muted" style={{ fontSize: "0.8rem" }}>
                    {d.doc_id}
                  </div>
                </td>
                <td>{d.subject || "—"}</td>
                <td className="muted">{d.tags.join(", ") || "—"}</td>
                <td>{d.chunk_count}</td>
                <td className="muted">{d.latest_ts}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
