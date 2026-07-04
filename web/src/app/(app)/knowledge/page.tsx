// The knowledge document list. Server-fetches the corpus + subjects, then a client
// component renders the table/cards with subject (hard) and tag (soft) filters.

import Link from "next/link";
import { EmptyState, PageHeader, PlusIcon, StatusMessage } from "@carneirofc/ui";

import { KnowledgeList } from "@carneirofc/magi-web/components/KnowledgeList";
import { listKnowledgeDocuments, listSubjects } from "@carneirofc/magi-web/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function KnowledgePage() {
  let documents: Awaited<ReturnType<typeof listKnowledgeDocuments>>["documents"] = [];
  let subjects: string[] = [];
  let error: string | null = null;
  try {
    [documents, subjects] = await Promise.all([
      listKnowledgeDocuments().then((d) => d.documents),
      listSubjects().then((s) => s.subjects.map((x) => x.name)),
    ]);
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        subtitle="magi // knowledge"
        title="Knowledge"
        description="The shared, read-only document corpus the model searches — chunked and embedded faithfully so retrieval returns source text."
      >
        <Link
          href="/knowledge/add"
          className="cyber-button inline-flex items-center gap-1 rounded-xl px-3 py-2 text-ui-xs font-medium no-underline text-[color:var(--text-0)]"
        >
          <PlusIcon /> Add document
        </Link>
      </PageHeader>

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : documents.length === 0 ? (
        <EmptyState>No documents in the corpus yet.</EmptyState>
      ) : (
        <KnowledgeList documents={documents} subjects={subjects} />
      )}
    </div>
  );
}
