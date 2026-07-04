"use client";

// Edit the operator memory settings: where memory lives on disk and whether it's a
// git-versioned repository. These are read once at startup, so a save is persisted
// immediately but only takes effect on the next restart — the editor surfaces that
// (and shows the directory the process is actually running from when they differ).
// Saves go through the admin BFF with the optimistic-concurrency version.

import { useState } from "react";
import { Checkbox, OutlineButton, StatusMessage, TextInput } from "@carneirofc/ui";

import type { AdminMemorySettings } from "../lib/admin-api";

export function MemorySettingsEditor({ initial }: { initial: AdminMemorySettings }) {
  const [memoryDir, setMemoryDir] = useState(initial.memory_dir);
  const [gitEnabled, setGitEnabled] = useState(initial.git_enabled);
  const [authorName, setAuthorName] = useState(initial.git_author_name);
  const [authorEmail, setAuthorEmail] = useState(initial.git_author_email);
  const [version, setVersion] = useState(initial.version);
  const [activeDir, setActiveDir] = useState(initial.active_memory_dir);
  const [restartRequired, setRestartRequired] = useState(initial.restart_required);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function apply(next: AdminMemorySettings) {
    setMemoryDir(next.memory_dir);
    setGitEnabled(next.git_enabled);
    setAuthorName(next.git_author_name);
    setAuthorEmail(next.git_author_email);
    setVersion(next.version);
    setActiveDir(next.active_memory_dir);
    setRestartRequired(next.restart_required);
  }

  function touched() {
    setDirty(true);
    setSaved(false);
  }

  function explain(status: number): string {
    if (status === 409) return "Changed elsewhere since you loaded — reload the page.";
    if (status === 503) return "Settings storage isn't available in this deployment.";
    return `Request failed (${status}).`;
  }

  async function save() {
    setError(null);
    setSaved(false);
    setBusy(true);
    const res = await fetch("/api/admin/settings/memory", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        memory_dir: memoryDir,
        git_enabled: gitEnabled,
        git_author_name: authorName,
        git_author_email: authorEmail,
        expectedVersion: version,
      }),
    });
    setBusy(false);
    if (res.ok) {
      apply((await res.json()) as AdminMemorySettings);
      setDirty(false);
      setSaved(true);
      return;
    }
    setError(explain(res.status));
  }

  const labelClass =
    "text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]";

  return (
    <div className="flex flex-col gap-6">
      {/* These apply at startup, so a saved change is pending until the next restart. */}
      {restartRequired ? (
        <StatusMessage role="status" tone="warn">
          Saved — restart the service to apply. It’s currently running from{" "}
          <code className="font-mono">{activeDir}</code>.
        </StatusMessage>
      ) : (
        <StatusMessage role="status" tone="info">
          Changes are saved immediately but take effect the next time the service
          restarts. Running from <code className="font-mono">{activeDir}</code>.
        </StatusMessage>
      )}

      <label className="flex flex-col gap-1">
        <span className={labelClass}>Memory directory</span>
        <TextInput
          value={memoryDir}
          onChange={(e) => {
            setMemoryDir(e.target.value);
            touched();
          }}
          placeholder="e.g. /var/lib/magi/memory or ~/magi-memory"
          className="max-w-xl font-mono"
          spellCheck={false}
        />
        <span className="text-ui-2xs text-[color:var(--ui-ink-subtle)]">
          Absolute path (or ~) outside the source tree. Empty = the built-in default.
        </span>
      </label>

      <div className="flex flex-col gap-3">
        <Checkbox
          label="Version memory with git (commit every change)"
          checked={gitEnabled}
          onChange={(e) => {
            setGitEnabled(e.target.checked);
            touched();
          }}
        />
        <span className="text-ui-2xs text-[color:var(--ui-ink-subtle)]">
          Requires the optional <code className="font-mono">git</code> extra. The
          directory must be its own top-level repo (not inside another one).
        </span>
      </div>

      <fieldset
        className="flex flex-col gap-3 border-0 p-0 disabled:opacity-60"
        disabled={!gitEnabled}
      >
        <div className="flex flex-wrap gap-4">
          <label className="flex flex-col gap-1">
            <span className={labelClass}>Commit author name</span>
            <TextInput
              value={authorName}
              onChange={(e) => {
                setAuthorName(e.target.value);
                touched();
              }}
              placeholder="magi-memory"
              className="max-w-xs"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={labelClass}>Commit author email</span>
            <TextInput
              value={authorEmail}
              onChange={(e) => {
                setAuthorEmail(e.target.value);
                touched();
              }}
              placeholder="magi-memory@localhost"
              className="max-w-xs"
            />
          </label>
        </div>
      </fieldset>

      <div className="flex items-center gap-3">
        <OutlineButton variant="accent" controlSize="md" onClick={save} disabled={busy || !dirty}>
          {busy ? "Saving…" : "Save"}
        </OutlineButton>
        {saved ? (
          <span className="text-ui-xs text-[color:var(--status-success-text)]">Saved.</span>
        ) : null}
      </div>

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : null}
    </div>
  );
}
