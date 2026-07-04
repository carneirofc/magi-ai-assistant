// BFF: the bot's presented identity (name/description/avatar flag), read from the
// chat-api. Handler logic lives in the library; this file mounts it at
// /api/identity.

export { GET } from "@carneirofc/magi-web/routes/identity";

export const dynamic = "force-dynamic";
