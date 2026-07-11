// BFF: search stored transcripts for the session rail. Handler logic lives in
// the library; this file mounts it at /api/chat/search.

export { GET } from "@carneirofc/magi-web/routes/chat/search";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
