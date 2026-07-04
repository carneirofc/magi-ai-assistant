// Operator settings — where memory lives on disk and whether it's git-versioned.
// These are read at startup, so changes here are saved immediately but apply on the
// next restart of the service (the editor says so, and shows the active directory).

import { PageHeader, StatusMessage, SurfacePanel } from "@carneirofc/ui";

import { MemorySettingsEditor } from "../components/MemorySettingsEditor";
import { getMemorySettings } from "../lib/admin-api";
import { mergeCopy, type PageCopy } from "../lib/page-copy";

export const settingsCopy = {
  subtitle: "magi // settings",
  title: "Settings",
  description:
    "Where the assistant's memory lives on disk and whether it's kept as a git-versioned history. These apply when the service restarts.",
} as const;

export async function SettingsView({ copy }: { copy?: PageCopy } = {}) {
  const header = mergeCopy(settingsCopy, copy);

  let settings: Awaited<ReturnType<typeof getMemorySettings>> | null = null;
  let error: string | null = null;
  try {
    settings = await getMemorySettings();
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader subtitle={header.subtitle} title={header.title} description={header.description} />

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

export default function SettingsPage() {
  return <SettingsView />;
}
