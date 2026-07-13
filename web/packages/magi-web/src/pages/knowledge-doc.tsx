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

import { AppPage } from "../components/AppPage";
import { CopyId } from "../components/CopyId";
import { DocumentActions } from "../components/DocumentActions";
import { DocumentMeta } from "../components/DocumentMeta";
import { ScrollRegion } from "../components/ScrollRegion";
import { getKnowledgeDocument, listSubjects, listTags } from "../lib/admin-api";
import { mergeCopy, type PageCopy } from "../lib/page-copy";

export async function DocumentView({
  params,
  copy,
}: {
  params: Promise<{ docId: string[] }>;
  copy?: PageCopy;
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

  // Header copy is data-driven (title = the doc's title); overrides still win.
  const header = mergeCopy(
    {
      subtitle: `magi // knowledge // ${doc.scope}`,
      title: doc.title,
      description: "A single document in the corpus — its metadata and indexed chunks.",
    },
    copy,
  );

  return (
    <AppPage className="gap-6">
      <div>
        <Link href="/knowledge" className="text-ui-xs">
          ← all documents
        </Link>
      </div>

      <PageHeader subtitle={header.subtitle} title={header.title} description={header.description} />

      <ScrollRegion className="flex flex-col gap-6">
      <CopyId value={doc.doc_id} className="self-start" />

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
      </ScrollRegion>
    </AppPage>
  );
}

export default function DocumentPage({ params }: { params: Promise<{ docId: string[] }> }) {
  return <DocumentView params={params} />;
}
