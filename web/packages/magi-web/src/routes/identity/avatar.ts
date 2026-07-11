// BFF: relay the bot's profile-picture bytes from the chat-api (404 when none is
// set). Referenced as <img src="/api/identity/avatar?v=<version>"> so a new upload
// busts the browser cache via the version query. `?mood=<name>` relays that
// mood's expression portrait instead (the identity expression pack; `neutral`
// aliases the avatar) — same versioned-URL caching, keyed per mood.

import { fetchIdentityAvatar } from "../../lib/chat-api";

export async function GET(request: Request) {
  const mood = new URL(request.url).searchParams.get("mood") ?? undefined;
  const res = await fetchIdentityAvatar(mood);
  if (!res.ok || !res.body) {
    return new Response("not found", { status: res.status === 422 ? 422 : 404 });
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
