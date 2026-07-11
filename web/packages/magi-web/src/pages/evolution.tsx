// Evolution page: the self-evolution approval queue — what the assistant has
// proposed to change about its own prompts and tooling, decided here. The
// page frame is server-rendered; the queue itself is live (client component).

import { PageHeader } from "@carneirofc/ui";

import { EvolutionQueue } from "../components/EvolutionQueue";
import { mergeCopy, type PageCopy } from "../lib/page-copy";

export const evolutionCopy = {
  subtitle: "magi // evolution",
  title: "Evolution",
  description:
    "Growth with a human in the loop: proposed prompt revisions and new tools wait here for your decision. Approved changes are versioned and apply on restart.",
} as const;

export function EvolutionPageView({ copy }: { copy?: PageCopy } = {}) {
  const header = mergeCopy(evolutionCopy, copy);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        subtitle={header.subtitle}
        title={header.title}
        description={header.description}
      />
      <EvolutionQueue />
    </div>
  );
}

export default function EvolutionPage() {
  return <EvolutionPageView />;
}
