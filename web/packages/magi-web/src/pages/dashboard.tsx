// Dashboard overview: at-a-glance metrics across memory + knowledge, plus recent
// documents and the busiest users. Server-fetches the three list endpoints.

import Link from "next/link";
import {
  EmptyState,
  InfoChip,
  PageHeader,
  StatusBadge,
  StatusMessage,
  SurfacePanel,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@carneirofc/ui";

import { StatCard } from "../components/StatCard";
import { encodeDocId } from "../lib/encode";
import { getHealth, listKnowledgeDocuments, listSubjects, listUsers } from "../lib/admin-api";
import { mergeCopy, type PageCopy } from "../lib/page-copy";

export const dashboardCopy = {
  subtitle: "magi // admin",
  title: "Dashboard",
  description:
    "Memory and knowledge at a glance — durable facts the assistant keeps and the shared document corpus it searches.",
} as const;

export async function DashboardView({ copy }: { copy?: PageCopy } = {}) {
  const header = mergeCopy(dashboardCopy, copy);

  let users: Awaited<ReturnType<typeof listUsers>>["users"] = [];
  let documents: Awaited<ReturnType<typeof listKnowledgeDocuments>>["documents"] = [];
  let subjects: string[] = [];
  let error: string | null = null;
  const health = await getHealth();
  try {
    const [u, d, s] = await Promise.all([
      listUsers().then((r) => r.users),
      listKnowledgeDocuments().then((r) => r.documents),
      listSubjects().then((r) => r.subjects.map((x) => x.name)),
    ]);
    users = u;
    documents = d;
    subjects = s;
  } catch {
    error = "Could not reach the admin API.";
  }

  const facts = users.reduce((n, u) => n + u.fact_count, 0);
  const episodes = users.reduce((n, u) => n + u.episode_count, 0);
  const sessions = users.reduce((n, u) => n + u.session_count, 0);
  const chunks = documents.reduce((n, d) => n + d.chunk_count, 0);

  const recentDocs = [...documents]
    .sort((a, b) => (a.latest_ts < b.latest_ts ? 1 : -1))
    .slice(0, 6);
  const topUsers = [...users].sort((a, b) => b.fact_count - a.fact_count).slice(0, 6);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        subtitle={header.subtitle}
        title={header.title}
        description={header.description}
        pills={
          <StatusBadge tone={health ? "success" : "error"}>
            <span
              className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full"
              style={{ background: "currentColor" }}
            />
            {health ? "Backend online" : "Backend offline"}
          </StatusBadge>
        }
      />

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : (
        <>
          <section className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard label="Users" value={users.length} hint="with memory" href="/memory" />
            <StatCard label="Facts" value={facts} hint="curated, across users" href="/memory" />
            <StatCard label="Episodes" value={episodes} hint="recorded interactions" />
            <StatCard label="Sessions" value={sessions} hint="on disk" />
            <StatCard
              label="Documents"
              value={documents.length}
              hint="in the corpus"
              href="/knowledge"
            />
            <StatCard label="Chunks" value={chunks} hint="embedded + indexed" href="/knowledge" />
            <StatCard label="Subjects" value={subjects.length} hint="controlled vocab" href="/subjects" />
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <SurfacePanel tone="soft" padding="lg" className="flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <h2 className="text-ui-lg font-semibold">Recent documents</h2>
                <Link href="/knowledge" className="text-ui-xs">
                  View all →
                </Link>
              </div>
              {recentDocs.length === 0 ? (
                <EmptyState>No documents in the corpus yet.</EmptyState>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Document</TableHead>
                      <TableHead>Subject</TableHead>
                      <TableHead className="text-right">Chunks</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {recentDocs.map((d) => (
                      <TableRow key={d.doc_id}>
                        <TableCell>
                          <Link href={`/knowledge/${encodeDocId(d.doc_id)}`}>
                            {d.title || d.source || d.doc_id}
                          </Link>
                        </TableCell>
                        <TableCell>
                          {d.subject ? (
                            <InfoChip>{d.subject}</InfoChip>
                          ) : (
                            <span className="text-[color:var(--ui-ink-subtle)]">—</span>
                          )}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{d.chunk_count}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </SurfacePanel>

            <SurfacePanel tone="soft" padding="lg" className="flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <h2 className="text-ui-lg font-semibold">Top users</h2>
                <Link href="/memory" className="text-ui-xs">
                  View all →
                </Link>
              </div>
              {topUsers.length === 0 ? (
                <EmptyState>No users with memory yet.</EmptyState>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>User</TableHead>
                      <TableHead className="text-right">Facts</TableHead>
                      <TableHead className="text-right">Episodes</TableHead>
                      <TableHead className="text-right">Sessions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {topUsers.map((u) => (
                      <TableRow key={u.user_id}>
                        <TableCell>
                          <Link href={`/memory/${encodeURIComponent(u.user_id)}`}>{u.user_id}</Link>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{u.fact_count}</TableCell>
                        <TableCell className="text-right tabular-nums">{u.episode_count}</TableCell>
                        <TableCell className="text-right tabular-nums">{u.session_count}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </SurfacePanel>
          </div>
        </>
      )}
    </div>
  );
}

export default function DashboardPage() {
  return <DashboardView />;
}
