// Add knowledge — paste text or upload a file, assign a subject + tags.

import Link from "next/link";
import { PageHeader, StatusMessage } from "@carneirofc/ui";

import { AddKnowledge } from "@carneirofc/magi-web/components/AddKnowledge";
import { listSubjects, listTags } from "@carneirofc/magi-web/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function AddKnowledgePage() {
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
    <div className="flex flex-col gap-6">
      <div>
        <Link href="/knowledge" className="text-ui-xs">
          ← all documents
        </Link>
      </div>
      <PageHeader
        subtitle="magi // knowledge"
        title="Add document"
        description="Paste text or upload a file. The content is chunked and embedded into the corpus on save."
      />
      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : (
        <AddKnowledge subjects={subjects} allTags={tags} />
      )}
    </div>
  );
}
