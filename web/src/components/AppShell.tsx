// Dashboard chrome shared by every authed page: fixed left rail + scrolling
// content column. Server component — the interactive bits (active nav, theme
// toggle) are their own client components.

import type { ReactNode } from "react";

import { Sidebar } from "./Sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <Sidebar />
      <div className="app-content">
        <div className="app-content-inner">{children}</div>
      </div>
    </div>
  );
}
