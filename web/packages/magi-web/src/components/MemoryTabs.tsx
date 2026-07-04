"use client";

// A tiny tab switcher for the user memory page. The published @carneirofc/ui
// (0.2.0) doesn't ship the Tabs primitive, so we drive three server-rendered
// panels with the available SegmentedControl. Panels arrive as props (React
// nodes) from the server page and are toggled client-side.

import { useState, type ReactNode } from "react";
import { SegmentedControl } from "@carneirofc/ui";

type TabKey = "facts" | "episodes" | "sessions";

export function MemoryTabs({
  facts,
  episodes,
  sessions,
  counts,
}: {
  facts: ReactNode;
  episodes: ReactNode;
  sessions: ReactNode;
  counts: { facts: number; episodes: number; sessions: number };
}) {
  const [tab, setTab] = useState<TabKey>("facts");
  const panels: Record<TabKey, ReactNode> = { facts, episodes, sessions };

  return (
    <div className="flex flex-col gap-4">
      <SegmentedControl<TabKey>
        value={tab}
        onValueChange={setTab}
        options={[
          { value: "facts", label: `Facts (${counts.facts})` },
          { value: "episodes", label: `Episodes (${counts.episodes})` },
          { value: "sessions", label: `Sessions (${counts.sessions})` },
        ]}
      />
      <div>{panels[tab]}</div>
    </div>
  );
}
