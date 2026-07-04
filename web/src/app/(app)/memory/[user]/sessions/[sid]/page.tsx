// One session's state. The view lives in the library; this file mounts it at
// /memory/[user]/sessions/[sid] (Next passes the route params through).

export { default } from "@carneirofc/magi-web/pages/memory-session";

export const dynamic = "force-dynamic";
