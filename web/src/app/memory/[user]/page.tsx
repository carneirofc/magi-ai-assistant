// A user's memory: curated facts (per-fact CRUD), raw long-term, editable
// episodes, and a session list.

import Link from "next/link";

import { FactEditor } from "@/components/FactEditor";
import { Nav } from "@/components/Nav";
import { RawFileEditor } from "@/components/RawFileEditor";
import { getProfile, getRawFile, listSessions } from "@/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function UserMemoryPage({
  params,
}: {
  params: Promise<{ user: string }>;
}) {
  const { user } = await params;
  const userId = decodeURIComponent(user);

  let profile: Awaited<ReturnType<typeof getProfile>> | null = null;
  let episodesFile: Awaited<ReturnType<typeof getRawFile>> | null = null;
  let sessions: string[] = [];
  let error: string | null = null;
  try {
    [profile, episodesFile, sessions] = await Promise.all([
      getProfile(userId),
      getRawFile("episodes", { userId }),
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
          {profile ? (
            <FactEditor
              userId={userId}
              initialFacts={profile.facts}
              initialVersion={profile.version}
            />
          ) : null}

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

          {episodesFile ? (
            <RawFileEditor
              kind="episodes"
              label="Episodes"
              userId={userId}
              initialContent={episodesFile.content}
              initialVersion={episodesFile.version}
            />
          ) : null}

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
