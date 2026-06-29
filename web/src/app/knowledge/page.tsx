// The knowledge document list — slice 1's one feature. A server component that
// calls the admin-api directly (server-side, with the bearer), so the token never
// touches the browser. Future slices add detail/edit/add views.

import { Nav } from "@/components/Nav";
import { listKnowledgeDocuments } from "@/lib/admin-api";

export const dynamic = "force-dynamic"; // always live; never cached at build

export default async function KnowledgePage() {
  let documents: Awaited<ReturnType<typeof listKnowledgeDocuments>>["documents"] = [];
  let error: string | null = null;
  try {
    documents = (await listKnowledgeDocuments()).documents;
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
        <table>
          <thead>
            <tr>
              <th>Document</th>
              <th>Scope</th>
              <th>Chunks</th>
              <th>Last ingested</th>
            </tr>
          </thead>
          <tbody>
            {documents.map((d) => (
              <tr key={d.doc_id}>
                <td>
                  <div>{d.source || d.doc_id}</div>
                  <div className="muted" style={{ fontSize: "0.8rem" }}>
                    {d.doc_id}
                  </div>
                </td>
                <td>{d.scope}</td>
                <td>{d.chunk_count}</td>
                <td className="muted">{d.latest_ts}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
