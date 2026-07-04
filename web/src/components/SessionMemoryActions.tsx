"use client";

// Operator triggers for one session's machine-managed memory passes: fold the
// pending buffer into the rolling summary, run the durable-memory curator over
// that summary, or flush the session into an episode. Each posts to the BFF and
// refreshes the server-rendered files on a change. The two model-backed passes
// (summarize, curate) surface a friendly note when the deployment has no model.

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ConfirmationDialog, OutlineButton, StatusMessage } from "@carneirofc/ui";

type Action = "summarize" | "curate" | "flush";
type Result = { action: string; changed: boolean; detail: string };
type Tone = "success" | "warn" | "error";

export function SessionMemoryActions({
  userId,
  sessionId,
}: {
  userId: string;
  sessionId: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<Action | null>(null);
  const [status, setStatus] = useState<{ tone: Tone; text: string } | null>(null);
  const [confirmingFlush, setConfirmingFlush] = useState(false);

  async function run(action: Action) {
    setStatus(null);
    setBusy(action);
    let res: Response;
    try {
      res = await fetch("/api/admin/memory/session-trigger", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userId, sessionId, action }),
      });
    } catch {
      setBusy(null);
      setStatus({ tone: "error", text: "Could not reach the admin API." });
      return;
    }
    setBusy(null);
    if (res.ok) {
      const data = (await res.json()) as Result;
      setStatus({ tone: data.changed ? "success" : "warn", text: data.detail });
      if (data.changed) router.refresh();
      return;
    }
    if (res.status === 503) {
      setStatus({
        tone: "warn",
        text: "Unavailable in this deployment — no model is wired for this pass.",
      });
      return;
    }
    setStatus({ tone: "error", text: `Failed (${res.status}).` });
  }

  return (
    <div className="flex flex-col gap-3">
      <div>
        <h2 className="text-ui-md font-semibold">Run a memory pass</h2>
        <p className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
          The same passes the chat path runs automatically — triggered on demand for this session.
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <OutlineButton
          variant="accent"
          controlSize="md"
          onClick={() => run("summarize")}
          disabled={busy !== null}
        >
          {busy === "summarize" ? "Summarizing…" : "Summarize now"}
        </OutlineButton>
        <OutlineButton
          variant="accent"
          controlSize="md"
          onClick={() => run("curate")}
          disabled={busy !== null}
        >
          {busy === "curate" ? "Curating…" : "Curate from summary"}
        </OutlineButton>
        <OutlineButton
          variant="danger"
          controlSize="md"
          onClick={() => setConfirmingFlush(true)}
          disabled={busy !== null}
        >
          {busy === "flush" ? "Flushing…" : "Flush session"}
        </OutlineButton>
      </div>

      {status ? (
        <StatusMessage role="status" tone={status.tone}>
          {status.text}
        </StatusMessage>
      ) : null}

      {confirmingFlush ? (
        <ConfirmationDialog
          dialog={{
            title: "Flush this session?",
            details: [
              "The live turn window is wiped and the rolling summary is carried into a durable episode.",
            ],
            outcomes: ["The live window and pending buffer for this session are cleared."],
            confirmLabel: "Flush",
          }}
          onClose={async (accepted) => {
            setConfirmingFlush(false);
            if (accepted) await run("flush");
          }}
        />
      ) : null}
    </div>
  );
}
