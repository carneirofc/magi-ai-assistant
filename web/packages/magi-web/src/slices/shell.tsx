import type { ReactNode } from "react";

import { AppShell } from "../components/AppShell";
import { DEFAULT_NAV, type NavItem } from "../components/Sidebar";
import { defineSliceNavContribution } from "./core";

export type AppNavItem = NavItem;

export const defaultNavContribution = defineSliceNavContribution({
  slice: "app",
  items: DEFAULT_NAV,
});

export { AppShell, DEFAULT_NAV };

export function buildDefaultAppShellConfig(
  overrides: Partial<{ brand: string; tagline: string; logo: string; nav: NavItem[] }> = {},
) {
  return {
    brand: overrides.brand,
    tagline: overrides.tagline,
    logo: overrides.logo,
    nav: overrides.nav ?? DEFAULT_NAV,
  };
}

export function MagiAppShell({
  children,
  nav,
  brand,
  tagline,
  logo,
}: {
  children: ReactNode;
  nav?: NavItem[];
  brand?: string;
  tagline?: string;
  logo?: string;
}) {
  return (
    <AppShell nav={nav ?? DEFAULT_NAV} brand={brand} tagline={tagline} logo={logo}>
      {children}
    </AppShell>
  );
}