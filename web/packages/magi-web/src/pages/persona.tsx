// The global persona — the bot's evolved behavior, shared across all users.
// Editable as a raw file.

import { PageHeader, StatusMessage, SurfacePanel } from "@carneirofc/ui";

import { RawFileEditor } from "../components/RawFileEditor";
import { getRawFile } from "../lib/admin-api";
import { mergeCopy, type PageCopy } from "../lib/page-copy";

export const personaCopy = {
  subtitle: "magi // persona",
  title: "Persona",
  description:
    "The global personality and evolved behavioral adjustments — one shared profile, not scoped to any user.",
} as const;

export async function PersonaView({ copy }: { copy?: PageCopy } = {}) {
  const header = mergeCopy(personaCopy, copy);

  let file: Awaited<ReturnType<typeof getRawFile>> | null = null;
  let error: string | null = null;
  try {
    file = await getRawFile("persona");
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

export default function PersonaPage() {
  return <PersonaView />;
}
