"use client";

// Create / rename / delete subjects (the controlled vocabulary). Calls the BFF
// subject routes and refreshes the server-rendered list.

import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  ConfirmationDialog,
  EditIcon,
  EmptyState,
  InfoChip,
  OutlineButton,
  StatusMessage,
  SurfacePanel,
  TextInput,
  TrashIcon,
} from "@carneirofc/ui";

type Subject = { id: string; name: string; description: string };

export function SubjectManager({ subjects }: { subjects: Subject[] }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<Subject | null>(null);
  const [editName, setEditName] = useState("");
  const [pendingDelete, setPendingDelete] = useState<Subject | null>(null);
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

  async function rename(id: string, next: string) {
    const res = await fetch(`/api/admin/subjects/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: next }),
    });
    if (res.ok) router.refresh();
    else setError(`Rename failed (${res.status}).`);
  }

  async function remove(id: string) {
    const res = await fetch(`/api/admin/subjects/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (res.ok) router.refresh();
    else setError(`Delete failed (${res.status}).`);
  }

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={create} className="flex flex-col gap-2 sm:flex-row">
        <TextInput
          aria-label="New subject"
          placeholder="New subject name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="flex-1"
        />
        <OutlineButton type="submit" variant="accent" controlSize="md" disabled={!name.trim()}>
          Add subject
        </OutlineButton>
      </form>

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : null}

      {subjects.length === 0 ? (
        <EmptyState>No subjects yet — add one above to start grouping documents.</EmptyState>
      ) : (
        <ul className="flex flex-col gap-2">
          {subjects.map((s) => (
            <li key={s.id}>
              <SurfacePanel tone="soft" padding="md">
                {editing?.id === s.id ? (
                  <div className="flex items-center gap-2">
                    <TextInput
                      autoFocus
                      controlSize="sm"
                      value={editName}
                      onChange={(e) => setEditName(e.target.value)}
                      className="flex-1"
                    />
                    <OutlineButton controlSize="sm" onClick={() => setEditing(null)}>
                      Cancel
                    </OutlineButton>
                    <OutlineButton
                      variant="accent"
                      controlSize="sm"
                      disabled={!editName.trim() || editName === s.name}
                      onClick={async () => {
                        await rename(s.id, editName.trim());
                        setEditing(null);
                      }}
                    >
                      Save
                    </OutlineButton>
                  </div>
                ) : (
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <InfoChip>{s.name}</InfoChip>
                      {s.description ? (
                        <span className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
                          {s.description}
                        </span>
                      ) : null}
                    </div>
                    <div className="flex gap-1">
                      <OutlineButton
                        controlSize="icon"
                        aria-label={`Rename ${s.name}`}
                        onClick={() => {
                          setEditing(s);
                          setEditName(s.name);
                        }}
                      >
                        <EditIcon />
                      </OutlineButton>
                      <OutlineButton
                        variant="danger"
                        controlSize="icon"
                        aria-label={`Delete ${s.name}`}
                        onClick={() => setPendingDelete(s)}
                      >
                        <TrashIcon />
                      </OutlineButton>
                    </div>
                  </div>
                )}
              </SurfacePanel>
            </li>
          ))}
        </ul>
      )}

      {pendingDelete ? (
        <ConfirmationDialog
          dialog={{
            title: `Delete subject "${pendingDelete.name}"?`,
            details: [`Subject: ${pendingDelete.name}`],
            outcomes: ["Documents keep their label; the subject leaves the controlled vocabulary."],
            confirmLabel: "Delete subject",
          }}
          onClose={(accepted) => {
            const target = pendingDelete;
            setPendingDelete(null);
            if (accepted && target) void remove(target.id);
          }}
        />
      ) : null}
    </div>
  );
}
