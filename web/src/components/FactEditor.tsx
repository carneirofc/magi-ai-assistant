"use client";

// Per-fact CRUD on a user's curated profile, rendered as mem0-style memory cards.
// Holds the optimistic-concurrency version locally and advances it from each
// write's response, so consecutive edits don't 409 against themselves; a real
// conflict (the curator wrote meanwhile) surfaces as a 409 prompt to reload.

import { useState } from "react";
import {
  ConfirmationDialog,
  EditIcon,
  EmptyState,
  OutlineButton,
  PlusIcon,
  StatusMessage,
  SurfacePanel,
  TextAreaInput,
  TextInput,
  TrashIcon,
} from "@carneirofc/ui";

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
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Fact | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(method: string, payload: object): Promise<boolean> {
    setError(null);
    setBusy(true);
    const res = await fetch("/api/admin/facts", {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ userId, expectedVersion: version, ...payload }),
    });
    setBusy(false);
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
    if (!draft.trim()) return;
    if (await send("POST", { text: draft.trim() })) setDraft("");
  }

  function startEdit(f: Fact) {
    setEditingId(f.id);
    setEditText(f.text);
  }

  async function saveEdit(f: Fact) {
    const next = editText.trim();
    if (next && next !== f.text) {
      if (!(await send("PATCH", { factId: f.id, text: next }))) return;
    }
    setEditingId(null);
  }

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={add} className="flex flex-col gap-2 sm:flex-row">
        <TextInput
          aria-label="New fact"
          placeholder="Add a memory — a durable fact about this user…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          className="flex-1"
        />
        <OutlineButton
          type="submit"
          variant="accent"
          controlSize="md"
          disabled={busy || !draft.trim()}
        >
          <PlusIcon /> Add memory
        </OutlineButton>
      </form>

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : null}

      {facts.length === 0 ? (
        <EmptyState>No curated memories for this user yet.</EmptyState>
      ) : (
        <ul className="flex flex-col gap-2">
          {facts.map((f) => (
            <li key={f.id}>
              <SurfacePanel tone="soft" padding="md" className="flex flex-col gap-2">
                {editingId === f.id ? (
                  <>
                    <TextAreaInput
                      aria-label="Edit memory"
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      rows={3}
                      autoFocus
                    />
                    <div className="flex justify-end gap-2">
                      <OutlineButton controlSize="sm" onClick={() => setEditingId(null)}>
                        Cancel
                      </OutlineButton>
                      <OutlineButton
                        variant="accent"
                        controlSize="sm"
                        disabled={busy || !editText.trim()}
                        onClick={() => saveEdit(f)}
                      >
                        Save
                      </OutlineButton>
                    </div>
                  </>
                ) : (
                  <div className="flex items-start justify-between gap-3">
                    <p className="min-w-0 text-ui-sm text-[color:var(--ui-ink)]">{f.text}</p>
                    <div className="flex shrink-0 gap-1">
                      <OutlineButton
                        controlSize="icon"
                        aria-label="Edit memory"
                        onClick={() => startEdit(f)}
                      >
                        <EditIcon />
                      </OutlineButton>
                      <OutlineButton
                        variant="danger"
                        controlSize="icon"
                        aria-label="Delete memory"
                        onClick={() => setPendingDelete(f)}
                      >
                        <TrashIcon />
                      </OutlineButton>
                    </div>
                  </div>
                )}
                {f.ts ? (
                  <p className="text-ui-2xs text-[color:var(--ui-ink-subtle)]">{f.ts}</p>
                ) : null}
              </SurfacePanel>
            </li>
          ))}
        </ul>
      )}

      {pendingDelete ? (
        <ConfirmationDialog
          dialog={{
            title: "Delete this memory?",
            details: [pendingDelete.text],
            outcomes: ["The fact is removed from the user's curated profile."],
            confirmLabel: "Delete",
          }}
          onClose={async (accepted) => {
            const target = pendingDelete;
            setPendingDelete(null);
            if (accepted && target) await send("DELETE", { factId: target.id });
          }}
        />
      ) : null}
    </div>
  );
}
