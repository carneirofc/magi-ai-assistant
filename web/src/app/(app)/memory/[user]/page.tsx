// A user's memory: curated facts (mem0-style cards), raw long-term, editable
// episodes, and a session list — organized under tabs.

import Link from "next/link";
import { EmptyState, InfoChip, PageHeader, StatusMessage, SurfacePanel } from "@carneirofc/ui";

import { FactEditor } from "@/components/FactEditor";
import { MemoryTabs } from "@/components/MemoryTabs";
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
    <div className="flex flex-col gap-6">
      <div>
        <Link href="/memory" className="text-ui-xs">
          ← all users
        </Link>
      </div>
      <PageHeader
        subtitle="magi // memory"
        title={userId}
        description="Durable memory for this user."
        pills={
          profile ? (
            <>
              <InfoChip>{profile.facts.length} facts</InfoChip>
              <InfoChip>{profile.episodes.length} episodes</InfoChip>
              <InfoChip>{sessions.length} sessions</InfoChip>
            </>
          ) : undefined
        }
      />

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : profile ? (
        <MemoryTabs
          counts={{
            facts: profile.facts.length,
            episodes: profile.episodes.length,
            sessions: sessions.length,
          }}
          facts={
            <div className="flex flex-col gap-4">
              <FactEditor
                userId={userId}
                initialFacts={profile.facts}
                initialVersion={profile.version}
              />
              {profile.raw_long_term.length > 0 ? (
                <SurfacePanel tone="subtle" padding="lg" className="flex flex-col gap-2">
                  <h2 className="ui-text-label-sm text-[color:var(--ui-ink-accent)]">
                    Raw long-term
                  </h2>
                  <ul className="flex flex-col gap-1 text-ui-sm text-[color:var(--ui-ink-muted)]">
                    {profile.raw_long_term.map((t, i) => (
                      <li key={i}>{t}</li>
                    ))}
                  </ul>
                </SurfacePanel>
              ) : null}
            </div>
          }
          episodes={
            episodesFile ? (
              <RawFileEditor
                kind="episodes"
                label="Episodes"
                description="The gist of past conversations, one per recorded interaction."
                userId={userId}
                initialContent={episodesFile.content}
                initialVersion={episodesFile.version}
              />
            ) : (
              <EmptyState>No episodes file.</EmptyState>
            )
          }
          sessions={
            sessions.length > 0 ? (
              <ul className="flex flex-col gap-2">
                {sessions.map((sid) => (
                  <li key={sid}>
                    <Link
                      href={`/memory/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(sid)}`}
                      className="no-underline"
                    >
                      <SurfacePanel
                        tone="soft"
                        padding="md"
                        className="flex items-center justify-between transition-colors hover:border-ui-active"
                      >
                        <span className="font-mono text-ui-xs text-[color:var(--ui-ink)]">{sid}</span>
                        <span className="text-ui-xs text-[color:var(--ui-ink-subtle)]">Edit →</span>
                      </SurfacePanel>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState>No sessions on disk.</EmptyState>
            )
          }
        />
      ) : null}
    </div>
  );
}
