// A single knowledge document: its fields + chunks in order. Read-only (rename /
// retag / delete arrive in later slices). Catch-all segment because a doc_id is
// the ingest path and may contain slashes.

import Link from "next/link";
import { notFound } from "next/navigation";

import { DocumentActions } from "@/components/DocumentActions";
import { DocumentMeta } from "@/components/DocumentMeta";
import { Nav } from "@/components/Nav";
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
    <main>
      <Nav title="Knowledge" />
      <p>
        <Link href="/knowledge">← all documents</Link>
      </p>

      <h1 style={{ marginBottom: "0.25rem" }}>{doc.title}</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        {doc.doc_id} · {doc.scope}
      </p>

      <DocumentActions docId={doc.doc_id} title={doc.title} />

      <DocumentMeta
        docId={doc.doc_id}
        subject={doc.subject}
        tags={doc.tags}
        allSubjects={allSubjects}
        allTags={allTags}
      />

      <dl style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "0.25rem 1rem" }}>
        <dt className="muted">Source</dt>
        <dd>{doc.source}</dd>
        <dt className="muted">Chunks</dt>
        <dd>{doc.chunks.length}</dd>
      </dl>

      <h2>Chunks</h2>
      {doc.chunks.map((c) => (
        <div
          key={c.chunk_index}
          style={{
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: "0.75rem 1rem",
            margin: "0.5rem 0",
          }}
        >
          <div className="muted" style={{ fontSize: "0.8rem", marginBottom: "0.4rem" }}>
            #{c.chunk_index}
          </div>
          <div style={{ whiteSpace: "pre-wrap" }}>{c.text}</div>
        </div>
      ))}
    </main>
  );
}
