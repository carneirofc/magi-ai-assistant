// Auth gate: every route except the public allow-list requires a valid session
// cookie, else redirect to /login. The cookie is verified here (Web Crypto works
// in the edge runtime) so unauthenticated requests never reach a page or a BFF
// proxy route.
//
// Mechanism only: the consuming app mounts this from its own `middleware.ts` and
// owns the `config.matcher` (which paths to run on) — see the reference app. The
// public allow-list defaults to the login page + login route; an overlay can pass
// its own.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { session, verifySession } from "./lib/session";

export const DEFAULT_PUBLIC_PATHS = ["/login", "/api/auth/login"];

/** Build an auth-gate middleware. Requests to `publicPaths` pass through; every
 * other request needs a valid session cookie or is redirected to /login. */
export function createAuthMiddleware(publicPaths: readonly string[] = DEFAULT_PUBLIC_PATHS) {
  return async function middleware(req: NextRequest) {
    const { pathname } = req.nextUrl;
    if (publicPaths.includes(pathname)) {
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
  };
}

/** The default auth gate (login page + login route are public). */
export const middleware = createAuthMiddleware();
