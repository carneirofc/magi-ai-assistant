// BFF: upload / clear / read the bot's profile picture. Handler logic lives in the
// library; this file mounts it at /api/admin/identity/avatar.

export { DELETE, GET, PUT } from "@carneirofc/magi-web/routes/admin/identity/avatar";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
