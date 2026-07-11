"use client";

// McpServerManager — the operator's MCP registry editor. Two lists: the
// code-declared servers (read-only context, edited in main.py) and the
// operator's own entries, edited here as JSON (they merge over the code list
// by name at team assembly, so an operator can add a server or disable a
// code one — `{"name": "comfyui", "enabled": false}`). The team assembles at
// startup, so a save is honest about needing a restart; live connection
// status stays on the roster above (chat-api introspection).
//
// JSON-as-editor on purpose: the spec is an open dict (transports, headers,
// allowlists, roles) and a form would either lag the engine or dumb it down.

import { useEffect, useState } from "react";
import { OutlineButton, StatusMessage, TextAreaInput } from "@carneirofc/ui";

type McpSettings = {
  code_servers: Record<string, unknown>[];
  operator_servers: Record<string, unknown>[];
  version: string;
  restart_required?: boolean;
};

export function McpServerManager() {
  const [settings, setSettings] = useState<McpSettings | null>(null);
  const [text, setText] = useState("");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "success" | "warn" | "error"; text: string } | null>(
    null,
  );

  useEffect(() => {
    let active = true;
    fetch("/api/admin/settings/mcp", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`${res.status}`))))
      .then((body: McpSettings) => {
        if (!active) return;
        setSettings(body);
        setText(JSON.stringify(body.operator_servers, null, 2));
      })
      .catch(() => {
        if (active) setNote({ tone: "error", text: "Couldn't load the MCP settings." });
      });
    return () => {
      active = false;
    };
  }, []);

  async function save() {
    if (!settings) return;
    let servers: unknown;
    try {
      servers = JSON.parse(text);
    } catch {
      setNote({ tone: "error", text: "Not valid JSON." });
      return;
    }
    if (!Array.isArray(servers)) {
      setNote({ tone: "error", text: "Expected a JSON list of server specs." });
      return;
    }
    setBusy(true);
    setNote(null);
    const res = await fetch("/api/admin/settings/mcp", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ servers, expected_version: settings.version }),
    });
    setBusy(false);
    if (res.ok) {
      const body = (await res.json()) as McpSettings;
      setSettings(body);
      setText(JSON.stringify(body.operator_servers, null, 2));
      setDirty(false);
      setNote({ tone: "success", text: "Saved — restart the app to apply." });
      return;
    }
    if (res.status === 409) setNote({ tone: "error", text: "Changed elsewhere — reload." });
    else if (res.status === 422) setNote({ tone: "error", text: "Every server needs a name." });
    else setNote({ tone: "error", text: `Save failed (${res.status}).` });
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h2 className="text-ui-md font-semibold">MCP servers</h2>
        <p className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
          Wire any Model Context Protocol server as a specialist (or lead tools) without
          code. Operator entries merge over the code-declared list by name; changes
          apply on restart. Connection status shows on the roster above.
        </p>
      </div>

      {settings && settings.code_servers.length > 0 ? (
        <div className="rounded-lg border border-ui bg-[color:var(--ui-bg-soft)] px-3 py-2">
          <p className="text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
            Declared in code (main.py)
          </p>
          <ul className="mt-1 flex flex-col gap-0.5">
            {settings.code_servers.map((s, i) => (
              <li key={i} className="font-mono text-ui-2xs text-[color:var(--ui-ink-muted)]">
                {String(s.name ?? "?")} — {String(s.url ?? s.command ?? "")}{" "}
                <span className="opacity-60">({String(s.attach ?? "member")})</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <TextAreaInput
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setDirty(true);
        }}
        rows={Math.min(18, Math.max(6, text.split("\n").length + 1))}
        spellCheck={false}
        className="font-mono text-ui-2xs"
        aria-label="Operator MCP server list (JSON)"
        placeholder={'[\n  {"name": "my-server", "url": "http://127.0.0.1:9000/mcp", "attach": "member"}\n]'}
      />
      <div className="flex items-center gap-3">
        <OutlineButton
          variant="accent"
          controlSize="md"
          onClick={() => void save()}
          disabled={busy || !dirty || !settings}
        >
          {busy ? "Saving…" : "Save"}
        </OutlineButton>
      </div>
      {note ? (
        <StatusMessage role="status" tone={note.tone}>
          {note.text}
        </StatusMessage>
      ) : null}
    </div>
  );
}
