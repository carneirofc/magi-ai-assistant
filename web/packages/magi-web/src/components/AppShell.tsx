"use client";

// Dashboard chrome shared by every authed page: collapsible left rail + a
// scrolling content column with a breadcrumb top bar. Client component because it
// owns the rail's collapsed state (persisted to localStorage so it survives
// navigations and reloads). Children are passed through untouched, so pages can
// still be server components.

import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { Sidebar, type NavItem } from "./Sidebar";
import { Topbar } from "./Topbar";

const STORAGE_KEY = "magi:rail-collapsed";

type AppShellProps = {
  children: ReactNode;
  /** Brand wordmark for the rail (default "MAGI"). */
  brand?: string;
  /** Small caps tagline under the wordmark (default "Admin"). */
  tagline?: string;
  /** Single-glyph logo mark (default the brand's first letter). */
  logo?: string;
  /** Nav items (default the reference app's routes). */
  nav?: NavItem[];
};

export function AppShell({ children, brand, tagline, logo, nav }: AppShellProps) {
  const [collapsed, setCollapsed] = useState(false);

  // Restore the last choice on mount. Reading localStorage during render would
  // desync SSR/CSR, so we start expanded and correct after hydration.
  useEffect(() => {
    setCollapsed(window.localStorage.getItem(STORAGE_KEY) === "1");
  }, []);

  const toggle = () => {
    setCollapsed((prev) => {
      const next = !prev;
      window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
      return next;
    });
  };

  return (
    <div className="app-shell" data-collapsed={collapsed}>
      <Sidebar
        collapsed={collapsed}
        onToggle={toggle}
        brand={brand}
        tagline={tagline}
        logo={logo}
        nav={nav}
      />
      <div className="app-content">
        <div className="app-content-inner">
          <Topbar />
          {children}
        </div>
      </div>
    </div>
  );
}
