"use client";

// Edit the BFF's runtime configuration — the backend URLs, bearer tokens, and
// storage locations that used to be env-only. Values resolve file override → env
// → built-in default (see lib/runtime-config.ts); this editor shows where each
// one currently comes from and lets the operator override it (or reset back to
// the environment). Saves go through the admin BFF as a { set, clear } patch.
//
// `live` fields apply on the next request; `restart` fields are read at startup,
// so a save is stored immediately but only takes effect after a restart — each
// such field is badged so the operator knows. Secret values are never sent to the
// browser: a secret field shows only whether it's set, and typing replaces it.

import { useMemo, useState } from "react";
import { OutlineButton, StatusMessage, TextInput } from "@carneirofc/ui";

import type { ConfigFieldState, ConfigKey, ConfigState } from "../lib/runtime-config";

const GROUP_LABELS: Record<ConfigFieldState["group"], string> = {
  connection: "Backend connection",
  app: "Application",
};

const GROUP_ORDER: ConfigFieldState["group"][] = ["connection", "app"];

const labelClass =
  "text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]";
const hintClass = "text-ui-2xs text-[color:var(--ui-ink-subtle)]";

function sourceLabel(field: ConfigFieldState): string {
  if (field.source === "file") return "overridden here";
  if (field.source === "env") return `from env (${field.env})`;
  return "built-in default";
}

export function ConnectionSettingsEditor({ initial }: { initial: ConfigState }) {
  const [fields, setFields] = useState<ConfigFieldState[]>(initial.fields);
  // Editable text per field: non-secrets seed with the resolved value; secrets
  // start blank (typing is what sets a new value).
  const [draft, setDraft] = useState<Record<string, string>>(() =>
    Object.fromEntries(fields.map((f) => [f.key, f.secret ? "" : (f.value ?? "")])),
  );
  // Keys the operator explicitly reset back to env/default.
  const [cleared, setCleared] = useState<Record<string, boolean>>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const restartPending = useMemo(
    () =>
      fields.some(
        (f) =>
          f.apply === "restart" &&
          (cleared[f.key] || (f.secret ? !!draft[f.key] : draft[f.key] !== (f.value ?? ""))),
      ),
    [fields, draft, cleared],
  );

  function touch() {
    setDirty(true);
    setSaved(false);
  }

  function setValue(key: ConfigKey, value: string) {
    setDraft((d) => ({ ...d, [key]: value }));
    if (cleared[key]) setCleared((c) => ({ ...c, [key]: false }));
    touch();
  }

  function resetToEnv(key: ConfigKey) {
    setCleared((c) => ({ ...c, [key]: true }));
    setDraft((d) => ({ ...d, [key]: "" }));
    touch();
  }

  function buildPatch(): { set: Partial<Record<ConfigKey, string>>; clear: ConfigKey[] } {
    const set: Partial<Record<ConfigKey, string>> = {};
    const clear: ConfigKey[] = [];
    for (const f of fields) {
      if (cleared[f.key]) {
        if (f.overridden) clear.push(f.key);
        continue;
      }
      const v = draft[f.key] ?? "";
      if (f.secret) {
        if (v.trim() !== "") set[f.key] = v;
      } else if (v !== (f.value ?? "")) {
        if (v.trim() === "") {
          if (f.overridden) clear.push(f.key);
        } else {
          set[f.key] = v;
        }
      }
    }
    return { set, clear };
  }

  async function save() {
    setError(null);
    setSaved(false);
    setBusy(true);
    const res = await fetch("/api/admin/settings/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPatch()),
    });
    setBusy(false);
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      setError(body.error ?? `Request failed (${res.status}).`);
      return;
    }
    const next = (await res.json()) as ConfigState;
    setFields(next.fields);
    setDraft(
      Object.fromEntries(next.fields.map((f) => [f.key, f.secret ? "" : (f.value ?? "")])),
    );
    setCleared({});
    setDirty(false);
    setSaved(true);
  }

  return (
    <div className="flex flex-col gap-6">
      <StatusMessage role="status" tone="info">
        These layer over the deployment’s environment variables — a saved value
        wins over its env var, and clearing an override falls back to it. Live
        fields apply on the next request; fields marked{" "}
        <span className="font-semibold">restart required</span> only take effect
        after the service restarts.
      </StatusMessage>

      {GROUP_ORDER.map((group) => {
        const groupFields = fields.filter((f) => f.group === group);
        if (groupFields.length === 0) return null;
        return (
          <section key={group} className="flex flex-col gap-4">
            <h3 className={labelClass}>{GROUP_LABELS[group]}</h3>
            {groupFields.map((f) => (
              <label key={f.key} className="flex flex-col gap-1">
                <span className="flex flex-wrap items-center gap-2">
                  <span className={labelClass}>{f.label}</span>
                  {f.apply === "restart" ? (
                    <span className="rounded-full border border-[color:var(--status-warn-text)] px-2 py-0.5 text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--status-warn-text)]">
                      restart required
                    </span>
                  ) : null}
                </span>
                <TextInput
                  type={f.secret ? "password" : "text"}
                  value={draft[f.key] ?? ""}
                  onChange={(e) => setValue(f.key, e.target.value)}
                  placeholder={
                    f.secret
                      ? f.isSet
                        ? "•••••••• — set (type to replace)"
                        : "not set"
                      : f.env
                  }
                  className="max-w-xl font-mono"
                  spellCheck={false}
                  autoComplete="off"
                />
                <span className={hintClass}>
                  {f.help ? `${f.help} ` : ""}
                  <span className="italic">Currently {sourceLabel(f)}.</span>
                  {f.overridden && !cleared[f.key] ? (
                    <>
                      {" "}
                      <button
                        type="button"
                        onClick={() => resetToEnv(f.key)}
                        className="underline underline-offset-2 hover:opacity-80"
                      >
                        Reset to environment
                      </button>
                    </>
                  ) : null}
                  {cleared[f.key] ? (
                    <span className="italic"> Will reset to environment on save.</span>
                  ) : null}
                </span>
              </label>
            ))}
          </section>
        );
      })}

      {restartPending ? (
        <StatusMessage role="status" tone="warn">
          A restart-required field has changed — save persists it now, but restart
          the service to apply it.
        </StatusMessage>
      ) : null}

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
