"use client";

// Slim top bar for the content column: a breadcrumb trail derived from the path,
// giving the dashboard a consistent "where am I" anchor above every page.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Fragment } from "react";

const LABELS: Record<string, string> = {
  memory: "Memory",
  knowledge: "Knowledge",
  subjects: "Subjects",
  persona: "Persona",
  add: "Add",
  sessions: "Session",
};

function labelFor(segment: string): string {
  if (LABELS[segment]) return LABELS[segment];
  // decode ids/users; keep them short
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export function Topbar() {
  const pathname = usePathname() ?? "/";
  const segments = pathname.split("/").filter(Boolean);

  const crumbs = segments.map((seg, i) => ({
    label: labelFor(seg),
    href: "/" + segments.slice(0, i + 1).join("/"),
    last: i === segments.length - 1,
  }));

  return (
    <div className="flex h-12 items-center gap-1.5 text-ui-xs text-[color:var(--ui-ink-subtle)]">
      <Link href="/" className="hover:text-[color:var(--ui-ink)]">
        Home
      </Link>
      {crumbs.map((c) => (
        <Fragment key={c.href}>
          <span aria-hidden className="text-[color:var(--ui-ink-faint)]">
            /
          </span>
          {c.last ? (
            <span className="max-w-[16rem] truncate font-medium text-[color:var(--ui-ink)]">
              {c.label}
            </span>
          ) : (
            <Link href={c.href} className="max-w-[12rem] truncate hover:text-[color:var(--ui-ink)]">
              {c.label}
            </Link>
          )}
        </Fragment>
      ))}
    </div>
  );
}
