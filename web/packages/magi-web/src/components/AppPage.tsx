// A page laid out to fill the app frame exactly and delegate ALL scrolling to the
// ScrollRegion(s) it contains. Under the fixed-frame model every authed page is an
// AppPage: it fills the content column, hides its own overflow, and never grows a
// frame-level scrollbar.
//
// Deliberately freeform — it is just the fill container (the flex `min-h-0` chain
// baked in) with no prescribed header/body/footer slots. The author drops fixed
// chrome (a PageHeader) and one or more <ScrollRegion>s wherever the layout needs
// them, so a single-body page and a two-panel page compose the same primitive.
//
// Works in both frame modes: under Alyssa's fixed frame (content column is
// overflow:hidden) it is the fill; under a still-column-scroll frame it simply
// fills one viewport with nothing extra to scroll — so migrating a shared page to
// AppPage is safe before an app flips its frame.

import type { HTMLAttributes, ReactNode } from "react";

export type AppPageProps = HTMLAttributes<HTMLDivElement> & {
  children?: ReactNode;
};

export function AppPage({ className = "", children, ...rest }: AppPageProps) {
  return (
    <div
      data-app-page=""
      className={`flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden ${className}`}
      {...rest}
    >
      {children}
    </div>
  );
}
