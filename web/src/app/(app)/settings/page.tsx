// Operator settings — where memory lives on disk and whether it's git-versioned.
// These are read at startup, so changes here are saved immediately but apply on the
// next restart of the service (the editor says so, and shows the active directory).

import { PageHeader, StatusMessage, SurfacePanel } from "@carneirofc/ui";

import { MemorySettingsEditor } from "@carneirofc/magi-web/components/MemorySettingsEditor";
import { getMemorySettings } from "@carneirofc/magi-web/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  let settings: Awaited<ReturnType<typeof getMemorySettings>> | null = null;
  let error: string | null = null;
  try {
    settings = await getMemorySettings();
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        subtitle="magi // settings"
        title="Settings"
        description="Where the assistant's memory lives on disk and whether it's kept as a git-versioned history. These apply when the service restarts."
      />

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : settings ? (
        <SurfacePanel tone="soft" padding="lg">
          <MemorySettingsEditor initial={settings} />
        </SurfacePanel>
      ) : null}
    </div>
  );
}
