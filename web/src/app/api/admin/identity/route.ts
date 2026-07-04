// BFF: read + update the bot identity's name/description. Handler logic lives in
// the library; this file mounts it at /api/admin/identity.

export { GET, PUT } from "@carneirofc/magi-web/routes/admin/identity";

export const dynamic = "force-dynamic";
