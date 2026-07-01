// The global persona — the bot's evolved behavior, shared across all users.
// Editable as a raw file.

import { Nav } from "@/components/Nav";
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
    <main>
      <Nav title="Persona" />
      {error ? (
        <p className="error">{error}</p>
      ) : file ? (
        <RawFileEditor
          kind="persona"
          label="Persona (global)"
          initialContent={file.content}
          initialVersion={file.version}
        />
      ) : null}
    </main>
  );
}
