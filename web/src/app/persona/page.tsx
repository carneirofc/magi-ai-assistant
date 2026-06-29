// The global persona — the bot's evolved behavior, shared across all users.
// Read-only here (editing arrives in a later slice).

import { Nav } from "@/components/Nav";
import { getPersona } from "@/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function PersonaPage() {
  let text = "";
  let error: string | null = null;
  try {
    text = (await getPersona()).text;
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <main>
      <Nav title="Persona" />
      {error ? (
        <p className="error">{error}</p>
      ) : text ? (
        <pre
          style={{
            whiteSpace: "pre-wrap",
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: "1rem",
          }}
        >
          {text}
        </pre>
      ) : (
        <p className="muted">No persona written yet.</p>
      )}
    </main>
  );
}
