"use client";

// The dashboard left rail: brand, primary nav (active-route aware), and sign-out.
// Client component because active highlighting needs the current pathname.

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

type NavItem = { href: string; label: string; icon: ReactNode; match: (p: string) => boolean };

const stroke = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round",
  strokeLinejoin: "round",
} as const;

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" className="h-[18px] w-[18px] shrink-0" aria-hidden {...stroke}>
      {children}
    </svg>
  );
}

const NAV: NavItem[] = [
  {
    href: "/",
    label: "Dashboard",
    match: (p) => p === "/",
    icon: (
      <Icon>
        <rect x="3" y="3" width="7" height="9" rx="1.5" />
        <rect x="14" y="3" width="7" height="5" rx="1.5" />
        <rect x="14" y="12" width="7" height="9" rx="1.5" />
        <rect x="3" y="16" width="7" height="5" rx="1.5" />
      </Icon>
    ),
  },
  {
    href: "/chat",
    label: "Chat",
    match: (p) => p.startsWith("/chat"),
    icon: (
      <Icon>
        <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v8A1.5 1.5 0 0 1 18.5 15H9l-4 4z" />
        <path d="M8 8.5h8" />
        <path d="M8 11.5h5" />
      </Icon>
    ),
  },
  {
    href: "/memory",
    label: "Memory",
    match: (p) => p.startsWith("/memory"),
    icon: (
      <Icon>
        <path d="M9.5 4.5a3 3 0 0 0-3 3v.2a3 3 0 0 0-1.5 5.2 3 3 0 0 0 1.9 4.6A2.5 2.5 0 0 0 12 19.5V6.5a2 2 0 0 0-2.5-2z" />
        <path d="M14.5 4.5a3 3 0 0 1 3 3v.2a3 3 0 0 1 1.5 5.2 3 3 0 0 1-1.9 4.6A2.5 2.5 0 0 1 12 19.5" />
      </Icon>
    ),
  },
  {
    href: "/team",
    label: "Team",
    match: (p) => p.startsWith("/team"),
    icon: (
      <Icon>
        <circle cx="9" cy="8" r="3" />
        <path d="M3.5 19a5.5 5.5 0 0 1 11 0" />
        <path d="M16 6.2a3 3 0 0 1 0 5.6" />
        <path d="M17.5 13.6a5.5 5.5 0 0 1 3 4.9" />
      </Icon>
    ),
  },
  {
    href: "/knowledge",
    label: "Knowledge",
    match: (p) => p.startsWith("/knowledge"),
    icon: (
      <Icon>
        <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H11a2 2 0 0 1 2 2v13a2 2 0 0 0-2-2H5.5A1.5 1.5 0 0 1 4 15.5z" />
        <path d="M20 5.5A1.5 1.5 0 0 0 18.5 4H13a2 2 0 0 0-2 2v13a2 2 0 0 1 2-2h5.5a1.5 1.5 0 0 0 1.5-1.5z" />
      </Icon>
    ),
  },
  {
    href: "/subjects",
    label: "Subjects",
    match: (p) => p.startsWith("/subjects"),
    icon: (
      <Icon>
        <path d="M3 7.5 5 5h5l2 2h7A1.5 1.5 0 0 1 20.5 8.5v9A1.5 1.5 0 0 1 19 19H5a2 2 0 0 1-2-2z" />
      </Icon>
    ),
  },
  {
    href: "/persona",
    label: "Persona",
    match: (p) => p.startsWith("/persona"),
    icon: (
      <Icon>
        <circle cx="12" cy="8" r="3.5" />
        <path d="M5 20a7 7 0 0 1 14 0" />
      </Icon>
    ),
  },
];

export function Sidebar() {
  const pathname = usePathname() ?? "/";

  return (
    <aside className="app-rail">
      <Link href="/" className="flex items-center gap-2 no-underline">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-[color:var(--ui-bg-active)] text-[color:var(--ui-ink-highlight)] text-ui-sm font-bold">
          M
        </span>
        <span className="flex flex-col leading-tight">
          <strong className="cyber-title text-ui-md">MAGI</strong>
          <span className="text-ui-2xs uppercase tracking-[0.18em] text-[color:var(--ui-ink-subtle)]">
            Admin
          </span>
        </span>
      </Link>

      <nav className="flex flex-1 flex-col gap-1">
        {NAV.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className="rail-link"
            data-active={item.match(pathname)}
            aria-current={item.match(pathname) ? "page" : undefined}
          >
            {item.icon}
            <span>{item.label}</span>
          </Link>
        ))}
      </nav>

      <form method="post" action="/api/auth/logout" className="mt-auto">
        <button
          type="submit"
          className="rail-link w-full cursor-pointer border-0 bg-transparent text-left text-[color:var(--ui-ink-subtle)]"
        >
          <Icon>
            <path d="M15 4h3a1.5 1.5 0 0 1 1.5 1.5v13A1.5 1.5 0 0 1 18 20h-3" />
            <path d="M10 8l-4 4 4 4" />
            <path d="M6 12h10" />
          </Icon>
          <span>Sign out</span>
        </button>
      </form>
    </aside>
  );
}
