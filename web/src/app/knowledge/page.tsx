// The knowledge document list. Server-fetches the corpus + subjects, then a client
// component renders the table with subject (hard) and tag (soft) filters.

import { KnowledgeList } from "@/components/KnowledgeList";
import { Nav } from "@/components/Nav";
import { listKnowledgeDocuments, listSubjects } from "@/lib/admin-api";

export const dynamic = "force-dynamic"; // always live; never cached at build

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
    <main>
      <Nav title="Knowledge" />
      {error ? (
        <p className="error">{error}</p>
      ) : documents.length === 0 ? (
        <p className="muted">No documents in the corpus yet.</p>
      ) : (
        <KnowledgeList documents={documents} subjects={subjects} />
      )}
    </main>
  );
}
