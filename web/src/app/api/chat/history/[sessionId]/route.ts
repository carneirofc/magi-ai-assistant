// BFF for chat transcript persistence. Handler logic lives in the library; this
// file mounts it at /api/chat/history/[sessionId].

export { GET, PUT, DELETE } from "@carneirofc/magi-web/routes/chat/history";

// Node runtime: this touches the filesystem. Never cache — it's mutable state.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
