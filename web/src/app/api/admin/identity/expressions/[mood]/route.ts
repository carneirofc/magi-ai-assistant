// BFF: manage one mood's expression portrait via the admin-api. Handler logic
// lives in the library; this file mounts it at /api/admin/identity/expressions/[mood].

export { DELETE, GET, PUT } from "@carneirofc/magi-web/routes/admin/identity/expression";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
