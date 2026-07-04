"use client";

// The memory user list as searchable cards. Client component so the operator can
// filter a long roster by id without a round-trip.

import Link from "next/link";
import { useMemo, useState } from "react";
import { EmptyState, SurfacePanel, TextInput } from "@carneirofc/ui";

type User = {
  user_id: string;
  fact_count: number;
  episode_count: number;
  session_count: number;
};

export function UserGrid({ users }: { users: User[] }) {
  const [q, setQ] = useState("");

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const list = needle
      ? users.filter((u) => u.user_id.toLowerCase().includes(needle))
      : users;
    return [...list].sort((a, b) => b.fact_count - a.fact_count);
  }, [users, q]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <TextInput
          aria-label="Search users"
          placeholder="Search users…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="max-w-xs"
        />
        <span className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
          {rows.length} of {users.length}
        </span>
      </div>

      {rows.length === 0 ? (
        <EmptyState>No users match “{q}”.</EmptyState>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {rows.map((u) => (
            <Link
              key={u.user_id}
              href={`/memory/${encodeURIComponent(u.user_id)}`}
              className="group rounded-xl no-underline outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--ui-ring-focus)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--ui-bg)]"
            >
              <SurfacePanel
                tone="soft"
                padding="lg"
                className="flex h-full flex-col gap-4 transition-[transform,border-color,box-shadow] duration-150 ease-out group-hover:-translate-y-0.5 group-hover:border-ui-active group-hover:shadow-[0_18px_30px_-22px_color-mix(in_oklab,var(--ui-border-active)_60%,transparent)] group-focus-visible:border-ui-active"
              >
                <div className="flex items-center gap-3">
                  <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[color:var(--ui-bg-active)] text-ui-md font-semibold text-[color:var(--ui-ink-highlight)] transition-transform duration-150 ease-out group-hover:scale-105">
                    {u.user_id.slice(0, 2).toUpperCase()}
                  </span>
                  <span className="min-w-0 truncate text-ui-sm font-semibold text-[color:var(--ui-ink)] transition-colors group-hover:text-[color:var(--ui-ink-accent)]">
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
    <div className="rounded-lg bg-[color:var(--ui-bg-soft)] px-2 py-2 transition-colors group-hover:bg-[color:var(--ui-bg-active)]">
      <p className="text-ui-lg font-semibold tabular-nums text-[color:var(--ui-ink)]">{value}</p>
      <p className="text-ui-2xs uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
        {label}
      </p>
    </div>
  );
}
