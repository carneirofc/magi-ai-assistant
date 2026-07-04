// BFF proxy for the chat console: relays the chat-api SSE stream and offloads the
// terminal `done` frame's media to the blob store. Handler logic lives in the
// library; this file mounts it at /api/chat.

export { POST } from "@carneirofc/magi-web/routes/chat";

// Node runtime + no caching: streaming must not be collapsed into a single body.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
