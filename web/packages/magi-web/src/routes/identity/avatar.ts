// BFF: relay the bot's profile-picture bytes from the chat-api (404 when none is
// set). Referenced as <img src="/api/identity/avatar?v=<version>"> so a new upload
// busts the browser cache via the version query.

import { fetchIdentityAvatar } from "../../lib/chat-api";

export async function GET() {
  const res = await fetchIdentityAvatar();
  if (!res.ok || !res.body) {
    return new Response("not found", { status: 404 });
  }
  return new Response(res.body, {
    status: 200,
    headers: {
      "Content-Type": res.headers.get("Content-Type") ?? "application/octet-stream",
      // Keyed by ?v=<version> at the call site, so this is safe to cache hard.
      "Cache-Control": "public, max-age=31536000, immutable",
    },
  });
}
