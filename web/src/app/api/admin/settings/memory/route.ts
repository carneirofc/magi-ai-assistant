// BFF: read + update operator memory settings. Handler logic lives in the library;
// this file mounts it at /api/admin/settings/memory.

export { GET, PUT } from "@carneirofc/magi-web/routes/admin/settings/memory";

export const dynamic = "force-dynamic";
