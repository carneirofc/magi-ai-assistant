// Overridable page-header copy. The library's page views ship with default copy
// (generic "magi // …" branding) and merge any overrides on top, so a persona
// overlay can reskin the copy without forking the page — the library owns no
// branding policy. Each `pages/*` module exports its defaults (e.g. `identityCopy`)
// and accepts a `copy?: PageCopy` prop.

export interface PageCopy {
  subtitle?: string;
  title?: string;
  description?: string;
}

/** Merge overrides over the page's default copy (undefined fields keep the default). */
export function mergeCopy(base: Required<PageCopy>, override?: PageCopy): Required<PageCopy> {
  return {
    subtitle: override?.subtitle ?? base.subtitle,
    title: override?.title ?? base.title,
    description: override?.description ?? base.description,
  };
}
