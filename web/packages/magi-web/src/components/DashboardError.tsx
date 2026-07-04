"use client";

// Error boundary body for the dashboard group — catches render/data errors in a
// page and offers a retry without a full reload. Mount it from an `error.tsx`
// (which must be a client component); the title/description are overridable.

import { useEffect } from "react";
import { OutlineButton, StatusMessage, SurfacePanel } from "@carneirofc/ui";

export function DashboardError({
  error,
  reset,
  title = "Something went wrong",
  description = "This page hit an error while rendering. It may be transient — try again.",
}: {
  error: Error & { digest?: string };
  reset: () => void;
  title?: string;
  description?: string;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <SurfacePanel tone="soft" padding="lg" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h1 className="cyber-title text-ui-lg font-semibold">{title}</h1>
        <p className="text-ui-sm text-[color:var(--ui-ink-muted)]">{description}</p>
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

export default DashboardError;
