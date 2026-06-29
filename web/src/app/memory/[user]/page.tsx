// A user's memory: curated facts, raw long-term, episodes, and a session list.
// Read-only (CRUD arrives in later slices).

import Link from "next/link";

import { Nav } from "@/components/Nav";
import { getProfile, listSessions } from "@/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function UserMemoryPage({
  params,
}: {
  params: Promise<{ user: string }>;
}) {
  const { user } = await params;
  const userId = decodeURIComponent(user);

  let profile: Awaited<ReturnType<typeof getProfile>> | null = null;
  let sessions: string[] = [];
  let error: string | null = null;
  try {
    [profile, sessions] = await Promise.all([
      getProfile(userId),
      listSessions(userId).then((s) => s.sessions),
    ]);
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <main>
      <Nav title={`Memory · ${userId}`} />
      <p>
        <Link href="/memory">← all users</Link>
      </p>

      {error ? (
        <p className="error">{error}</p>
      ) : (
        <>
          <section>
            <h2>Profile facts</h2>
            {profile && profile.facts.length > 0 ? (
              <ul>
                {profile.facts.map((f) => (
                  <li key={f.id}>
                    {f.text} <span className="muted">({f.id})</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No curated facts.</p>
            )}
          </section>

          {profile && profile.raw_long_term.length > 0 ? (
            <section>
              <h2>Raw long-term</h2>
              <ul>
                {profile.raw_long_term.map((t, i) => (
                  <li key={i}>{t}</li>
                ))}
              </ul>
            </section>
          ) : null}

          <section>
            <h2>Episodes</h2>
            {profile && profile.episodes.length > 0 ? (
              <ul>
                {profile.episodes.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            ) : (
              <p className="muted">No episodes.</p>
            )}
          </section>

          <section>
            <h2>Sessions</h2>
            {sessions.length > 0 ? (
              <ul>
                {sessions.map((sid) => (
                  <li key={sid}>
                    <Link
                      href={`/memory/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(
                        sid,
                      )}`}
                    >
                      {sid}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No sessions on disk.</p>
            )}
          </section>
        </>
      )}
    </main>
  );
}
