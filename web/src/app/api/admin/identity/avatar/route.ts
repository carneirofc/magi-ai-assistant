// BFF: upload / clear / read the bot's profile picture. Handler logic lives in the
// library; this file mounts it at /api/admin/identity/avatar.

export { GET, PUT, DELETE } from "@carneirofc/magi-web/routes/admin/identity/avatar";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
