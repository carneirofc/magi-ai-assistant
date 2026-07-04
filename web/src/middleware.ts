// Auth gate: every route except /login and the login API requires a valid
// session cookie, else redirect to /login. The cookie is verified here (Web
// Crypto works in the edge runtime) so unauthenticated requests never reach a
// page or a BFF proxy route.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { session, verifySession } from "@carneirofc/magi-web/lib/session";

const PUBLIC_PATHS = ["/login", "/api/auth/login"];

export async function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (PUBLIC_PATHS.includes(pathname)) {
    return NextResponse.next();
  }

  const token = req.cookies.get(session.cookieName)?.value;
  if (await verifySession(token)) {
    return NextResponse.next();
  }

  const url = req.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  return NextResponse.redirect(url);
}

export const config = {
  // Run on everything except Next internals and static assets.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
