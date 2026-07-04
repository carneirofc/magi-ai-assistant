// Dashboard chrome shared by every authed page: fixed left rail + scrolling
// content column with a breadcrumb top bar. Server component — the interactive
// bits (active nav, breadcrumbs, theme toggle) are their own client components.

import type { ReactNode } from "react";

import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <Sidebar />
      <div className="app-content">
        <div className="app-content-inner">
          <Topbar />
          {children}
        </div>
      </div>
    </div>
  );
}
