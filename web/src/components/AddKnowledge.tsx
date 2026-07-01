"use client";

// Add a knowledge document by pasting text or uploading a text file (the two
// resolvers in this slice; URL/connectors grow on the server later). Both produce
// {title, text}; subject is picked from the registry, tags are free-form.

import { useRouter } from "next/navigation";
import { useState } from "react";

export function AddKnowledge({ subjects }: { subjects: { id: string; name: string }[] }) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [subject, setSubject] = useState("");
  const [tags, setTags] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setText(await file.text());
    if (!title) setTitle(file.name);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setMsg(null);
    const res = await fetch("/api/admin/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title,
        text,
        subject,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      }),
    });
    setBusy(false);
    if (res.ok) {
      const data = (await res.json()) as { doc_id: string; chunks_indexed: number };
      setMsg(
        data.chunks_indexed > 0
          ? `Ingested "${data.doc_id}" (${data.chunks_indexed} chunks).`
          : `Saved "${data.doc_id}" but 0 chunks indexed — is the embedding/Qdrant backend up?`,
      );
      setTitle("");
      setText("");
      setTags("");
      router.refresh();
    } else {
      setError(res.status === 422 ? "Unknown subject." : `Failed (${res.status}).`);
    }
  }

  const field = {
    width: "100%",
    padding: "0.5rem 0.7rem",
    background: "#15171b",
    color: "var(--fg)",
    border: "1px solid var(--border)",
    borderRadius: 6,
    marginBottom: "0.6rem",
  } as const;

  return (
    <form onSubmit={submit} style={{ maxWidth: 640 }}>
      <input
        aria-label="Title"
        placeholder="Title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        style={field}
        required
      />
      <div style={{ marginBottom: "0.6rem" }}>
        <input type="file" accept=".md,.markdown,.txt,.rst,.text" onChange={onFile} />
        <span className="muted"> or paste below</span>
      </div>
      <textarea
        aria-label="Text"
        placeholder="Paste document text…"
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={10}
        style={{ ...field, fontFamily: "ui-monospace, monospace", fontSize: "0.85rem" }}
        required
      />
      <div style={{ display: "flex", gap: "0.6rem" }}>
        <select value={subject} onChange={(e) => setSubject(e.target.value)} style={{ ...field, width: "auto" }}>
          <option value="">— subject —</option>
          {subjects.map((s) => (
            <option key={s.id} value={s.name}>
              {s.name}
            </option>
          ))}
        </select>
        <input
          aria-label="Tags"
          placeholder="tags, comma, separated"
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          style={field}
        />
      </div>
      <button type="submit" disabled={busy || !title.trim() || !text.trim()}>
        {busy ? "Adding…" : "Add document"}
      </button>
      {msg ? <p className="muted">{msg}</p> : null}
      {error ? <p className="error">{error}</p> : null}
    </form>
  );
}
