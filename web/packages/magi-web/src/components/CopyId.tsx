"use client";

// Inline "copy to clipboard" affordance for identifiers (doc_id, session id).
// Shows the value in monospace with a copy button that flips to a check briefly.

import { useState } from "react";
import { CheckIcon, CopyIcon } from "@carneirofc/ui";

export function CopyId({ value, className }: { value: string; className?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard blocked — no-op */
    }
  }

  return (
    <span
      className={
        "inline-flex max-w-full items-center gap-1.5 rounded-md bg-[color:var(--ui-bg-soft)] px-2 py-1 " +
        (className ?? "")
      }
    >
      <span className="truncate font-mono text-ui-2xs text-[color:var(--ui-ink-muted)]">
        {value}
      </span>
      <button
        type="button"
        onClick={copy}
        aria-label="Copy to clipboard"
        title={copied ? "Copied" : "Copy"}
        className="cyber-icon-btn grid h-5 w-5 shrink-0 place-items-center rounded border border-ui-soft text-[color:var(--ui-ink-subtle)]"
      >
        {copied ? <CheckIcon className="h-3 w-3" /> : <CopyIcon className="h-3 w-3" />}
      </button>
    </span>
  );
}
