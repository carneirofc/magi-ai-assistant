"use client";

// Edit a document's subject (pick from the controlled vocabulary) and its tags
// (free-form, with corpus autocomplete). Calls the BFF doc-subject / doc-tags
// routes and refreshes. TagSelect hands back the full next selection; we diff it
// against the current tags to derive the add/remove the tags route expects.

import { useRouter } from "next/navigation";
import { useState } from "react";
import { SelectInput, StatusMessage, TagSelect } from "@carneirofc/ui";

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
  const [error, setError] = useState<string | null>(null);

  async function setDocSubject(next: string) {
    setError(null);
    const res = await fetch("/api/admin/knowledge/doc-subject", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ docId, subject: next }),
    });
    if (res.ok) router.refresh();
    else setError(`Subject change failed (${res.status}).`);
  }

  async function commitTags(next: string[]) {
    const cur = new Set(tags);
    const nextSet = new Set(next);
    const add = next.filter((t) => !cur.has(t));
    const remove = tags.filter((t) => !nextSet.has(t));
    if (add.length === 0 && remove.length === 0) return;
    setError(null);
    const res = await fetch("/api/admin/knowledge/doc-tags", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ docId, add, remove }),
    });
    if (res.ok) router.refresh();
    else setError(`Tag change failed (${res.status}).`);
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start gap-6">
        <label className="flex flex-col gap-1">
          <span className="ui-text-label-sm text-[color:var(--ui-ink-accent)]">Subject</span>
          <SelectInput
            controlSize="sm"
            value={subject}
            onChange={(e) => setDocSubject(e.target.value)}
          >
            <option value="">— none —</option>
            {allSubjects.map((s) => (
              <option key={s.id} value={s.name}>
                {s.name}
              </option>
            ))}
          </SelectInput>
        </label>

        <label className="flex min-w-[16rem] flex-1 flex-col gap-1">
          <span className="ui-text-label-sm text-[color:var(--ui-ink-accent)]">Tags</span>
          <TagSelect
            value={tags}
            onChange={commitTags}
            suggestions={allTags}
            placeholder="add tag…"
          />
        </label>
      </div>
      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : null}
    </div>
  );
}
