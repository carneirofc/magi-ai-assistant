// A single metric tile for the dashboard overview. Optionally a link.

import Link from "next/link";
import type { ReactNode } from "react";
import { SurfacePanel } from "@carneirofc/ui";

export function StatCard({
  label,
  value,
  hint,
  href,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  href?: string;
}) {
  const body = (
    <SurfacePanel
      tone="soft"
      padding="lg"
      className="h-full transition-colors hover:border-ui-active"
    >
      <p className="text-ui-2xs font-semibold uppercase tracking-[0.14em] text-[color:var(--ui-ink-subtle)]">
        {label}
      </p>
      <p className="cyber-title mt-2 text-ui-xl font-semibold tabular-nums">{value}</p>
      {hint ? <p className="mt-1 text-ui-xs text-[color:var(--ui-ink-muted)]">{hint}</p> : null}
    </SurfacePanel>
  );

  if (href) {
    return (
      <Link href={href} className="block no-underline">
        {body}
      </Link>
    );
  }
  return body;
}
