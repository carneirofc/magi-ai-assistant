// A single knowledge document. The view lives in the library; this file mounts it
// at /knowledge/[...docId] (catch-all; Next passes the params through).

export { default } from "@carneirofc/magi-web/pages/knowledge-doc";

export const dynamic = "force-dynamic";
