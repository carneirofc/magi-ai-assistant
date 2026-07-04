// BFF: relay the bot's profile-picture bytes from the chat-api. Handler logic lives
// in the library; this file mounts it at /api/identity/avatar.

export { GET } from "@carneirofc/magi-web/routes/identity/avatar";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
