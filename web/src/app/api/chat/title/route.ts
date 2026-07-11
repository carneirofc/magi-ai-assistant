// BFF: relay the auto-title pass. Handler logic lives in the library; this
// file mounts it at /api/chat/title.

export { POST } from "@carneirofc/magi-web/routes/chat/title";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
