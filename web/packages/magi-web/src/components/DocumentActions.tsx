"use client";

// Rename + delete controls for a knowledge document. Client component: calls the
// BFF mutation routes, then refreshes (rename) or navigates back (delete).

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { FormEvent } from "react";
import {
  ConfirmationDialog,
  OutlineButton,
  StatusMessage,
  TextInput,
  TrashIcon,
} from "@carneirofc/ui";

import { encodeDocId } from "../lib/encode";

export function DocumentActions({ docId, title }: { docId: string; title: string }) {
  const router = useRouter();
  const [value, setValue] = useState(title);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
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
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <form onSubmit={rename} className="flex items-center gap-2">
          <TextInput
            aria-label="Title"
            controlSize="sm"
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
          <OutlineButton
            type="submit"
            controlSize="sm"
            disabled={busy || !value.trim() || value === title}
          >
            Rename
          </OutlineButton>
        </form>
        <OutlineButton
          variant="danger"
          controlSize="sm"
          onClick={() => setConfirming(true)}
          disabled={busy}
        >
          <TrashIcon /> Delete
        </OutlineButton>
      </div>
      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : null}

      {confirming ? (
        <ConfirmationDialog
          dialog={{
            title: `Delete "${title}"?`,
            details: [`Document: ${docId}`],
            outcomes: ["The document and all of its indexed chunks are removed from the corpus."],
            confirmLabel: "Delete document",
          }}
          onClose={(accepted) => {
            setConfirming(false);
            if (accepted) void remove();
          }}
        />
      ) : null}
    </div>
  );
}
