// The ONLY element allowed to scroll inside the fixed app frame. Everything else
// — the shell, the content column, an AppPage — is locked to the viewport and
// hides its overflow, so content that can grow past the frame must live inside a
// ScrollRegion or it is clipped and unreachable.
//
// It bakes in the flex overflow plumbing (`min-h-0`/`min-w-0` + `flex-1`) that a
// scroll container needs to actually receive a bounded size from a flex parent —
// the classic "forgot min-height:0 and it grew instead of scrolling" trap. The
// `data-scroll-region` marker (set to the axis) is what the layout invariant test
// keys on: no element WITHOUT this attribute may have a computed overflow of
// auto/scroll.

import type { HTMLAttributes, ReactNode } from "react";

type Axis = "y" | "x" | "both";

// Lock the cross axis to `hidden` so a stray-wide child can't reintroduce a
// scrollbar on the axis the caller didn't ask for.
const AXIS_CLASS: Record<Axis, string> = {
  y: "overflow-y-auto overflow-x-hidden",
  x: "overflow-x-auto overflow-y-hidden",
  both: "overflow-auto",
};

export type ScrollRegionProps = HTMLAttributes<HTMLDivElement> & {
  /** Which axis scrolls. Defaults to vertical — the common case. */
  axis?: Axis;
  children?: ReactNode;
};

export function ScrollRegion({
  axis = "y",
  className = "",
  children,
  ...rest
}: ScrollRegionProps) {
  return (
    <div
      data-scroll-region={axis}
      className={`min-h-0 min-w-0 flex-1 ${AXIS_CLASS[axis]} ${className}`}
      {...rest}
    >
      {children}
    </div>
  );
}
