"use client";

// Add a knowledge document by pasting text or uploading a text file (the two
// resolvers in this slice; URL/connectors grow on the server later). Both produce
// {title, text}; subject is picked from the registry, tags are free-form.

import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  OutlineButton,
  SelectInput,
  StatusMessage,
  SurfacePanel,
  TagSelect,
  TextAreaInput,
  TextInput,
} from "@carneirofc/ui";

export function AddKnowledge({
  subjects,
  allTags = [],
}: {
  subjects: { id: string; name: string }[];
  allTags?: string[];
}) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [subject, setSubject] = useState("");
  const [tags, setTags] = useState<string[]>([]);
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
      body: JSON.stringify({ title, text, subject, tags }),
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
      setTags([]);
      router.refresh();
    } else {
      setError(res.status === 422 ? "Unknown subject." : `Failed (${res.status}).`);
    }
  }

  return (
    <SurfacePanel tone="soft" padding="lg" className="max-w-2xl">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className="ui-text-label-sm text-[color:var(--ui-ink-accent)]">Title</span>
          <TextInput
            placeholder="Document title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className="ui-text-label-sm text-[color:var(--ui-ink-accent)]">Content</span>
          <span className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
            Upload a text file, or paste below.
          </span>
          <input
            type="file"
            accept=".md,.markdown,.txt,.rst,.text"
            onChange={onFile}
            className="text-ui-xs text-[color:var(--ui-ink-muted)] file:mr-3 file:cursor-pointer file:rounded-lg file:border file:border-ui-strong file:bg-panel file:px-3 file:py-1.5 file:text-ui-xs"
          />
          <TextAreaInput
            placeholder="Paste document text…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={12}
            className="font-mono text-ui-xs"
            required
          />
        </label>

        <div className="flex flex-col gap-4 sm:flex-row">
          <label className="flex flex-col gap-1">
            <span className="ui-text-label-sm text-[color:var(--ui-ink-accent)]">Subject</span>
            <SelectInput value={subject} onChange={(e) => setSubject(e.target.value)}>
              <option value="">— none —</option>
              {subjects.map((s) => (
                <option key={s.id} value={s.name}>
                  {s.name}
                </option>
              ))}
            </SelectInput>
          </label>
          <label className="flex flex-1 flex-col gap-1">
            <span className="ui-text-label-sm text-[color:var(--ui-ink-accent)]">Tags</span>
            <TagSelect value={tags} onChange={setTags} suggestions={allTags} placeholder="add tag…" />
          </label>
        </div>

        <div className="flex items-center gap-3">
          <OutlineButton
            type="submit"
            variant="accent"
            controlSize="lg"
            disabled={busy || !title.trim() || !text.trim()}
          >
            {busy ? "Adding…" : "Add document"}
          </OutlineButton>
        </div>

        {msg ? (
          <StatusMessage role="status" tone="success">
            {msg}
          </StatusMessage>
        ) : null}
        {error ? (
          <StatusMessage role="alert" tone="error">
            {error}
          </StatusMessage>
        ) : null}
      </form>
    </SurfacePanel>
  );
}
