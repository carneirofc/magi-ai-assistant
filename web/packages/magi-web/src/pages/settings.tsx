// Operator settings. Two panels:
//   - Runtime configuration — the BFF's own wiring (backend URLs, bearer tokens,
//     storage locations) that used to be env-only, now overridable at runtime and
//     persisted to a JSON file (see lib/runtime-config.ts). Rendered from local
//     state, so it works even when the admin API is unreachable — which is exactly
//     when you'd need to fix its URL.
//   - Memory — where memory lives on disk and whether it's git-versioned, read
//     from the admin API. These apply on the next restart of the service.

import { PageHeader, StatusMessage, SurfacePanel } from "@carneirofc/ui";

import { AppPage } from "../components/AppPage";
import { ConnectionSettingsEditor } from "../components/ConnectionSettingsEditor";
import { MemorySettingsEditor } from "../components/MemorySettingsEditor";
import { ScrollRegion } from "../components/ScrollRegion";
import { getMemorySettings } from "../lib/admin-api";
import { readConfigState } from "../lib/runtime-config";
import { mergeCopy, type PageCopy } from "../lib/page-copy";

export const settingsCopy = {
  subtitle: "magi // settings",
  title: "Settings",
  description:
    "The BFF's runtime wiring — backend URLs, tokens, and storage locations — plus where the assistant's memory lives on disk.",
} as const;

const sectionLabelClass =
  "text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]";

export async function SettingsView({ copy }: { copy?: PageCopy } = {}) {
  const header = mergeCopy(settingsCopy, copy);

  // Local, always-available: never throws (falls back to env/defaults on a
  // missing file), so this panel renders regardless of backend health.
  const config = readConfigState();

  // Remote: the admin API may be down — degrade to an inline error rather than
  // failing the whole page (the runtime panel above can be used to repoint it).
  let memory: Awaited<ReturnType<typeof getMemorySettings>> | null = null;
  let memoryError: string | null = null;
  try {
    memory = await getMemorySettings();
  } catch {
    memoryError = "Could not reach the admin API.";
  }

  return (
    <AppPage className="gap-8">
      <PageHeader subtitle={header.subtitle} title={header.title} description={header.description} />

      <ScrollRegion className="flex flex-col gap-8">
        <section className="flex flex-col gap-3">
          <h2 className={sectionLabelClass}>Runtime configuration</h2>
          <SurfacePanel tone="soft" padding="lg">
            <ConnectionSettingsEditor initial={config} />
          </SurfacePanel>
        </section>

        <section className="flex flex-col gap-3">
          <h2 className={sectionLabelClass}>Memory</h2>
          {memoryError ? (
            <StatusMessage role="alert" tone="error">
              {memoryError}
            </StatusMessage>
          ) : memory ? (
            <SurfacePanel tone="soft" padding="lg">
              <MemorySettingsEditor initial={memory} />
            </SurfacePanel>
          ) : null}
        </section>
      </ScrollRegion>
    </AppPage>
  );
}

export default function SettingsPage() {
  return <SettingsView />;
}
