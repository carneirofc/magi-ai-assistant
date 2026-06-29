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
      scope: string;
      chunk_count: number;
      latest_ts: string;
    };
    DocumentList: {
      documents: components["schemas"]["DocumentSummaryOut"][];
    };
  };
}

export interface paths {
  "/admin/v1/knowledge/documents": {
    get: {
      responses: {
        200: {
          content: {
            "application/json": components["schemas"]["DocumentList"];
          };
        };
      };
    };
  };
}
