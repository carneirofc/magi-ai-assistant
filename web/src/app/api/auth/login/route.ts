// Login: set the signed session cookie when the posted password matches
// ADMIN_PASSWORD. Handler logic lives in the library; this file mounts it at
// /api/auth/login (also allow-listed in middleware).

export { POST } from "@carneirofc/magi-web/routes/auth/login";
