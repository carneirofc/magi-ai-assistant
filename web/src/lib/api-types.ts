// GENERATED FILE — placeholder.
//
// In normal operation this is overwritten by `pnpm gen:api`
// (openapi-typescript against the admin-api's /openapi.json). It is hand-stubbed
// here only because the scaffold was authored without a Node toolchain to run the
// generator. Regenerate it before relying on the types:
//
//     ADMIN_API_URL=http://127.0.0.1:8100 pnpm gen:api
//
// The shape below mirrors channels/admin.py's current contract so the app
// typechecks until the real generation runs.

export interface components {
  schemas: {
    DocumentSummaryOut: {
      doc_id: string;
      source: string;
      title: string;
      subject: string;
      tags: string[];
      scope: string;
      chunk_count: number;
      latest_ts: string;
    };
    DocumentList: {
      documents: components["schemas"]["DocumentSummaryOut"][];
    };
    ChunkOut: { chunk_index: number; text: string };
    DocumentDetailOut: {
      doc_id: string;
      source: string;
      title: string;
      subject: string;
      tags: string[];
      scope: string;
      chunks: components["schemas"]["ChunkOut"][];
    };
    UserSummary: {
      user_id: string;
      fact_count: number;
      episode_count: number;
      session_count: number;
    };
    UserList: { users: components["schemas"]["UserSummary"][] };
    Fact: { id: string; text: string; ts: string };
    Profile: {
      facts: components["schemas"]["Fact"][];
      raw_long_term: string[];
      episodes: string[];
    };
    SessionList: { sessions: string[] };
    Turn: { role: string; content: string; ts: string };
    SessionDetail: {
      turns: components["schemas"]["Turn"][];
      summary: string;
      pending: components["schemas"]["Turn"][];
    };
    Persona: { text: string };
    SubjectOut: { id: string; name: string; description: string };
    SubjectListOut: { subjects: components["schemas"]["SubjectOut"][] };
    TagList: { tags: string[] };
    Fact: { id: string; text: string; ts: string };
    FactsResult: { facts: components["schemas"]["Fact"][]; version: string };
    RawFile: { kind: string; content: string; version: string };
  };
}

type Json<T> = { get: { responses: { 200: { content: { "application/json": T } } } } };

export interface paths {
  "/admin/v1/knowledge/documents": Json<components["schemas"]["DocumentList"]>;
  "/admin/v1/knowledge/documents/{doc_id}": Json<
    components["schemas"]["DocumentDetailOut"]
  >;
  "/admin/v1/memory/users": Json<components["schemas"]["UserList"]>;
  "/admin/v1/memory/users/{user_id}/profile": Json<components["schemas"]["Profile"]>;
  "/admin/v1/memory/users/{user_id}/sessions": Json<components["schemas"]["SessionList"]>;
  "/admin/v1/memory/users/{user_id}/sessions/{session_id}": Json<
    components["schemas"]["SessionDetail"]
  >;
  "/admin/v1/memory/persona": Json<components["schemas"]["Persona"]>;
  "/admin/v1/knowledge/subjects": Json<components["schemas"]["SubjectListOut"]>;
  "/admin/v1/knowledge/tags": Json<components["schemas"]["TagList"]>;
  "/admin/v1/memory/files/{kind}": Json<components["schemas"]["RawFile"]>;
}
