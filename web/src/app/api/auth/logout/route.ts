// Logout: clear the session cookie and bounce to /login.

import { NextResponse } from "next/server";
import { cookies } from "next/headers";

import { session } from "@carneirofc/magi-web/lib/session";

export async function POST(req: Request) {
  const jar = await cookies();
  jar.delete(session.cookieName);
  return NextResponse.redirect(new URL("/login", req.url), { status: 303 });
}
