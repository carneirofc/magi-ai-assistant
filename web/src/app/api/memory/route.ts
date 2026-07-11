// BFF: relay the user's durable facts for the companion's memory panel. Handler
// logic lives in the library; this file mounts it at /api/memory.

export { GET } from "@carneirofc/magi-web/routes/chat/memory";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
