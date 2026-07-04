// Serves a chat media blob by id from the configured blob store. Handler logic
// lives in the library; this file mounts it at /api/chat/blobs/[id].

export { GET } from "@carneirofc/magi-web/routes/chat/blobs";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
