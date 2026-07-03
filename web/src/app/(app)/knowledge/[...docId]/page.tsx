// A single knowledge document: its fields + chunks in order, with rename/retag/
// delete controls. Catch-all segment because a doc_id is the ingest path and may
// contain slashes.

import Link from "next/link";
import { notFound } from "next/navigation";
import {
  InfoChip,
  KeyValueField,
  PageHeader,
  SectionLabel,
  SurfacePanel,
} from "@carneirofc/ui";

import { DocumentActions } from "@/components/DocumentActions";
import { DocumentMeta } from "@/components/DocumentMeta";
import { getKnowledgeDocument, listSubjects, listTags } from "@/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function DocumentPage({
  params,
}: {
  params: Promise<{ docId: string[] }>;
}) {
  const { docId } = await params;
  const id = docId.map(decodeURIComponent).join("/");

  let doc: Awaited<ReturnType<typeof getKnowledgeDocument>> | null = null;
  let allSubjects: { id: string; name: string }[] = [];
  let allTags: string[] = [];
  try {
    [doc, allSubjects, allTags] = await Promise.all([
      getKnowledgeDocument(id),
      listSubjects().then((s) => s.subjects),
      listTags().then((t) => t.tags),
    ]);
  } catch {
    notFound();
  }
  if (!doc) notFound();

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link href="/knowledge" className="text-ui-xs">
          ← all documents
        </Link>
      </div>

      <PageHeader
        subtitle={`magi // knowledge // ${doc.scope}`}
        title={doc.title}
        description={doc.doc_id}
      />

      <SurfacePanel tone="soft" padding="lg" className="flex flex-col gap-5">
        <DocumentActions docId={doc.doc_id} title={doc.title} />
        <DocumentMeta
          docId={doc.doc_id}
          subject={doc.subject}
          tags={doc.tags}
          allSubjects={allSubjects}
          allTags={allTags}
        />
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <KeyValueField label="Source" value={doc.source || "—"} />
          <KeyValueField label="Scope" value={doc.scope} />
          <KeyValueField label="Chunks" value={doc.chunks.length} />
          <KeyValueField label="Subject" value={doc.subject || "—"} />
        </div>
      </SurfacePanel>

      <div className="flex flex-col gap-2">
        <SectionLabel>Chunks ({doc.chunks.length})</SectionLabel>
        {doc.chunks.map((c) => (
          <SurfacePanel key={c.chunk_index} tone="subtle" padding="md" className="flex flex-col gap-2">
            <InfoChip className="self-start">#{c.chunk_index}</InfoChip>
            <div className="whitespace-pre-wrap text-ui-sm text-[color:var(--ui-ink-muted)]">
              {c.text}
            </div>
          </SurfacePanel>
        ))}
      </div>
    </div>
  );
}
