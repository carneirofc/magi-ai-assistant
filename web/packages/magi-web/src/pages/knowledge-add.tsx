// Add knowledge — paste text or upload a file, assign a subject + tags.

import Link from "next/link";
import { PageHeader, StatusMessage } from "@carneirofc/ui";

import { AddKnowledge } from "../components/AddKnowledge";
import { AppPage } from "../components/AppPage";
import { ScrollRegion } from "../components/ScrollRegion";
import { listSubjects, listTags } from "../lib/admin-api";
import { mergeCopy, type PageCopy } from "../lib/page-copy";

export const knowledgeAddCopy = {
  subtitle: "magi // knowledge",
  title: "Add document",
  description:
    "Paste text or upload a file. The content is chunked and embedded into the corpus on save.",
} as const;

export async function AddKnowledgeView({ copy }: { copy?: PageCopy } = {}) {
  const header = mergeCopy(knowledgeAddCopy, copy);

  let subjects: { id: string; name: string }[] = [];
  let tags: string[] = [];
  let error: string | null = null;
  try {
    [subjects, tags] = await Promise.all([
      listSubjects().then((s) => s.subjects),
      listTags().then((t) => t.tags),
    ]);
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <AppPage className="gap-6">
      <div>
        <Link href="/knowledge" className="text-ui-xs">
          ← all documents
        </Link>
      </div>
      <PageHeader subtitle={header.subtitle} title={header.title} description={header.description} />
      <ScrollRegion className="flex flex-col gap-6">
      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : (
        <AddKnowledge subjects={subjects} allTags={tags} />
      )}
      </ScrollRegion>
    </AppPage>
  );
}

export default function AddKnowledgePage() {
  return <AddKnowledgeView />;
}
