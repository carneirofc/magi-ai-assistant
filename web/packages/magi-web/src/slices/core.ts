import type { ComponentType, ReactNode } from "react";

export type StabilityTier = "stable" | "advanced" | "internal" | "legacy";

export interface SliceExportDescriptor {
  name: string;
  tier: StabilityTier;
  description: string;
}

export interface SliceEntryPoints {
  types: string;
  hooks: string;
  components: string;
  screens?: string;
  routes?: string;
}

export interface FeatureSliceContract<
  TTypes extends Record<string, unknown> = Record<string, unknown>,
  THooks extends Record<string, unknown> = Record<string, unknown>,
  TComponents extends Record<string, ComponentType<any>> = Record<string, ComponentType<any>>,
> {
  key: string;
  title: string;
  description: string;
  entrypoints: SliceEntryPoints;
  stable: {
    types: TTypes;
    hooks: THooks;
    components: TComponents;
  };
  advanced?: Partial<{
    types: Record<string, unknown>;
    hooks: Record<string, unknown>;
    components: Record<string, unknown>;
    screens: Record<string, unknown>;
    routes: Record<string, unknown>;
  }>;
  internalNotes?: string[];
}

export interface SliceNavItem {
  href: string;
  label: string;
  icon?: ReactNode;
  match?: (pathname: string) => boolean;
  visible?: boolean;
}

export interface SliceNavContribution {
  slice: string;
  items: SliceNavItem[];
}

export interface AppShellConfig {
  brand?: string;
  tagline?: string;
  logo?: string;
  nav: SliceNavItem[];
}

export function defineFeatureSlice<T extends FeatureSliceContract>(slice: T): T {
  return slice;
}

export function defineSliceNavContribution<T extends SliceNavContribution>(contribution: T): T {
  return contribution;
}

export function buildAppShellConfig(config: AppShellConfig): AppShellConfig {
  return {
    ...config,
    nav: config.nav.filter((item) => item.visible !== false),
  };
}

export const STABILITY_TIER_DOCS: Record<StabilityTier, string> = {
  stable:
    "Stable: developer-first imports we intend to teach and preserve across routine refactors.",
  advanced:
    "Advanced: supported lower-level seams for custom composition; still documented, but more likely to evolve.",
  internal:
    "Internal/Experimental: implementation details, useful for contributors but not part of the supported consumer contract.",
  legacy:
    "Legacy/Compatibility-only: old entrypoints kept temporarily as cheap re-exports while consumers migrate to slice imports.",
};
