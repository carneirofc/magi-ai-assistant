// The global persona — the bot's evolved behavior, shared across all users.
// Editable as a raw file.

import { PageHeader, StatusMessage, SurfacePanel } from "@carneirofc/ui";

import { RawFileEditor } from "@/components/RawFileEditor";
import { getRawFile } from "@/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function PersonaPage() {
  let file: Awaited<ReturnType<typeof getRawFile>> | null = null;
  let error: string | null = null;
  try {
    file = await getRawFile("persona");
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        subtitle="magi // persona"
        title="Persona"
        description="The global personality and evolved behavioral adjustments — one shared profile, not scoped to any user."
      />

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : file ? (
        <SurfacePanel tone="soft" padding="lg">
          <RawFileEditor
            kind="persona"
            label="Persona (global)"
            initialContent={file.content}
            initialVersion={file.version}
            maxRows={48}
          />
        </SurfacePanel>
      ) : null}
    </div>
  );
}
