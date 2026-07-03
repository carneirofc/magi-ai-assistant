// Memory: the user list as cards. Each links to that user's profile.

import Link from "next/link";
import { EmptyState, PageHeader, StatusMessage, SurfacePanel } from "@carneirofc/ui";

import { listUsers } from "@/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function MemoryPage() {
  let users: Awaited<ReturnType<typeof listUsers>>["users"] = [];
  let error: string | null = null;
  try {
    users = (await listUsers()).users;
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        subtitle="magi // memory"
        title="Memory"
        description="Every user the model keeps durable memory for — curated facts, recorded episodes, and live sessions."
      />

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : users.length === 0 ? (
        <EmptyState>No users with memory yet.</EmptyState>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {users.map((u) => (
            <Link
              key={u.user_id}
              href={`/memory/${encodeURIComponent(u.user_id)}`}
              className="no-underline"
            >
              <SurfacePanel
                tone="soft"
                padding="lg"
                className="flex h-full flex-col gap-4 transition-colors hover:border-ui-active"
              >
                <div className="flex items-center gap-3">
                  <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[color:var(--ui-bg-active)] text-ui-md font-semibold text-[color:var(--ui-ink-highlight)]">
                    {u.user_id.slice(0, 2).toUpperCase()}
                  </span>
                  <span className="min-w-0 truncate text-ui-sm font-semibold text-[color:var(--ui-ink)]">
                    {u.user_id}
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-2 text-center">
                  <Metric label="Facts" value={u.fact_count} />
                  <Metric label="Episodes" value={u.episode_count} />
                  <Metric label="Sessions" value={u.session_count} />
                </div>
              </SurfacePanel>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg bg-[color:var(--ui-bg-soft)] px-2 py-2">
      <p className="text-ui-lg font-semibold tabular-nums text-[color:var(--ui-ink)]">{value}</p>
      <p className="text-ui-2xs uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
        {label}
      </p>
    </div>
  );
}
