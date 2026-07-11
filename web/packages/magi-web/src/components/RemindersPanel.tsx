"use client";

// RemindersPanel — the companion's upcoming strip: pending reminders under the
// portrait (set in chat via the reminder tools). Quiet by design: renders
// nothing while loading, when the feature is off, or when there's nothing
// pending — the stage column stays calm unless there's something to say.

import { useEffect, useState } from "react";

type Reminder = { id: string; text: string; due: string; done?: boolean };

export function RemindersPanel({ userId }: { userId: string }) {
  const [reminders, setReminders] = useState<Reminder[]>([]);

  useEffect(() => {
    let active = true;
    fetch(`/api/chat/reminders?userId=${encodeURIComponent(userId)}`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { reminders?: Reminder[] } | null) => {
        if (!active || !body) return;
        setReminders((body.reminders ?? []).filter((r) => !r.done));
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [userId]);

  if (reminders.length === 0) return null;

  const now = new Date();
  return (
    <div className="rounded-xl border border-ui bg-[color:var(--ui-bg)] px-3 py-2">
      <p className="text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
        Reminders
      </p>
      <ul className="mt-1.5 flex flex-col gap-1">
        {reminders.slice(0, 5).map((r) => {
          const due = new Date(r.due);
          const overdue = !Number.isNaN(due.getTime()) && due <= now;
          return (
            <li key={r.id} className="flex items-baseline gap-2">
              <span
                aria-hidden
                className={`mt-0.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
                  overdue ? "bg-amber-500" : "bg-[color:var(--ui-ink-subtle)]"
                }`}
              />
              <span className="min-w-0 flex-1 truncate text-ui-2xs text-[color:var(--ui-ink)]">
                {r.text}
              </span>
              <span className="shrink-0 font-mono text-[10px] text-[color:var(--ui-ink-subtle)]">
                {r.due.slice(0, 10)}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
