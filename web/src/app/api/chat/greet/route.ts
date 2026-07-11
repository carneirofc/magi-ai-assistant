// BFF: relay the assistant-initiated greeting stream. Handler logic lives in
// the library; this file mounts it at /api/chat/greet.

export { POST } from "@carneirofc/magi-web/routes/chat/greet";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
