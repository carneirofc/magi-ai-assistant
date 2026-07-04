// BFF proxy: document mutations (rename, delete). Catch-all segment because a
// doc_id may contain slashes. Handler logic lives in the library; this file mounts
// it at /api/admin/knowledge/documents/[...docId].

export { PATCH, DELETE } from "@carneirofc/magi-web/routes/admin/knowledge/documents/doc";
