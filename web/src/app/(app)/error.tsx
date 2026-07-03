"use client";

// Error boundary for the dashboard group — catches render/data errors in a page
// and offers a retry without a full reload.

import { useEffect } from "react";
import { OutlineButton, StatusMessage, SurfacePanel } from "@carneirofc/ui";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <SurfacePanel tone="soft" padding="lg" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h1 className="cyber-title text-ui-lg font-semibold">Something went wrong</h1>
        <p className="text-ui-sm text-[color:var(--ui-ink-muted)]">
          This page hit an error while rendering. It may be transient — try again.
        </p>
      </div>
      <StatusMessage role="alert" tone="error">
        {error.message || "Unknown error."}
      </StatusMessage>
      <div>
        <OutlineButton variant="accent" controlSize="md" onClick={() => reset()}>
          Try again
        </OutlineButton>
      </div>
    </SurfacePanel>
  );
}
