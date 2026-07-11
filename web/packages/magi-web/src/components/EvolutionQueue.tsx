"use client";

// EvolutionQueue — the operator's decision surface for self-evolution: what
// the assistant asked to change about itself (prompt revisions, new HTTP tool
// recipes), each with its rationale and a current-vs-proposed view. Approve
// writes the runtime overlay (git-versioned when memory versioning is on) and
// takes effect on restart; reject just records the decision. The decided
// timeline stays visible below the pending queue — growth is auditable.

import { useCallback, useEffect, useState } from "react";
import { OutlineButton, StatusMessage } from "@carneirofc/ui";

type Proposal = {
  id: string;
  kind: string;
  target: string;
  current_text: string;
  proposed_text: string;
  rationale: string;
  source: string;
  status: string;
  created: string;
  decided: string;
  applied_path: string;
};

type QueueBody = { proposals: Proposal[]; proposable: string[] };

export function EvolutionQueue() {
  const [body, setBody] = useState<QueueBody | null>(null);
  const [state, setState] = useState<"loading" | "off" | "error" | "ready">("loading");
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const refresh = useCallback(() => {
    fetch("/api/admin/proposals", { cache: "no-store" })
      .then((res) => {
        if (res.status === 503) {
          setState("off");
          return null;
        }
        if (!res.ok) throw new Error(`${res.status}`);
        return res.json();
      })
      .then((data: QueueBody | null) => {
        if (data) {
          setBody(data);
          setState("ready");
        }
      })
      .catch(() => setState("error"));
  }, []);

  useEffect(() => refresh(), [refresh]);

  async function decide(id: string, action: "approve" | "reject") {
    setBusy(id);
    setNote(null);
    const res = await fetch("/api/admin/proposals", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, action }),
    });
    setBusy(null);
    if (res.ok) {
      setNote(
        action === "approve"
          ? "Approved — restart the app for it to take effect."
          : "Rejected.",
      );
      refresh();
      return;
    }
    setNote(res.status === 409 ? "Already decided — reloading." : `Failed (${res.status}).`);
    refresh();
  }

  if (state === "off") {
    return (
      <StatusMessage role="status" tone="warn">
        Self-evolution is not enabled in this deployment (evolution_enabled).
      </StatusMessage>
    );
  }
  if (state === "error") {
    return (
      <StatusMessage role="alert" tone="error">
        Could not reach the admin API.
      </StatusMessage>
    );
  }
  if (!body) return null;

  const pending = body.proposals.filter((p) => p.status === "pending");
  const decided = body.proposals.filter((p) => p.status !== "pending");

  return (
    <div className="flex flex-col gap-4">
      {note ? (
        <StatusMessage role="status" tone="success">
          {note}
        </StatusMessage>
      ) : null}

      <p className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
        Proposable prompts: {body.proposable.length ? body.proposable.join(", ") : "none"} ·
        identity prompts are never proposable. Approvals apply on restart.
      </p>

      {pending.length === 0 ? (
        <p className="text-ui-sm text-[color:var(--ui-ink-muted)]">
          Nothing pending — she hasn't asked to change anything.
        </p>
      ) : (
        pending.map((p) => (
          <div key={p.id} className="flex flex-col gap-2 rounded-xl border border-ui bg-[color:var(--ui-bg)] p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full border border-ui px-2 py-0.5 font-mono text-[10px] uppercase text-[color:var(--ui-ink-accent)]">
                {p.kind}
              </span>
              <span className="font-mono text-ui-xs text-[color:var(--ui-ink)]">{p.target}</span>
              <span className="ml-auto font-mono text-[10px] text-[color:var(--ui-ink-subtle)]">
                {p.created} · from {p.source}
              </span>
            </div>
            <p className="text-ui-xs text-[color:var(--ui-ink-muted)]">
              <span className="font-semibold">Why:</span> {p.rationale}
            </p>
            <div className="grid gap-2 lg:grid-cols-2">
              {p.current_text ? (
                <div className="min-w-0">
                  <p className="text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
                    Current
                  </p>
                  <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-ui bg-[color:var(--ui-bg-soft)] p-2 text-ui-2xs">
                    {p.current_text}
                  </pre>
                </div>
              ) : null}
              <div className="min-w-0">
                <p className="text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
                  Proposed
                </p>
                <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-[color:var(--ui-border-active)] bg-[color:var(--ui-bg-soft)] p-2 text-ui-2xs">
                  {p.proposed_text}
                </pre>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <OutlineButton
                variant="accent"
                controlSize="sm"
                disabled={busy === p.id}
                onClick={() => void decide(p.id, "approve")}
              >
                Approve
              </OutlineButton>
              <OutlineButton
                controlSize="sm"
                disabled={busy === p.id}
                onClick={() => void decide(p.id, "reject")}
              >
                Reject
              </OutlineButton>
            </div>
          </div>
        ))
      )}

      {decided.length > 0 ? (
        <details className="rounded-xl border border-ui bg-[color:var(--ui-bg)] px-3 py-2">
          <summary className="cursor-pointer text-ui-xs font-medium text-[color:var(--ui-ink-muted)]">
            Decided ({decided.length})
          </summary>
          <ul className="mt-2 flex flex-col gap-1">
            {decided.map((p) => (
              <li key={p.id} className="flex items-baseline gap-2 text-ui-2xs">
                <span
                  className={`font-mono uppercase ${
                    p.status === "approved"
                      ? "text-[color:var(--status-success-text,green)]"
                      : "text-[color:var(--ui-ink-subtle)]"
                  }`}
                >
                  {p.status}
                </span>
                <span className="font-mono text-[color:var(--ui-ink)]">{p.target}</span>
                <span className="min-w-0 flex-1 truncate text-[color:var(--ui-ink-muted)]">
                  {p.rationale}
                </span>
                <span className="font-mono text-[10px] text-[color:var(--ui-ink-subtle)]">
                  {p.decided}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
