// Layout for every authed dashboard page: wraps children in the sidebar shell.
// The /login route lives outside this group so it stays chrome-free.

import { AppShell } from "@/components/AppShell";

export default function AppGroupLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppShell>{children}</AppShell>;
}
