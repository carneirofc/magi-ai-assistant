// Login: compare the posted password against ADMIN_PASSWORD (server-side), and on
// match set the signed httpOnly session cookie. No accounts, one operator.

import { NextResponse } from "next/server";
import { cookies } from "next/headers";

import { session, signSession } from "../../lib/session";

export async function POST(req: Request) {
  const expected = process.env.ADMIN_PASSWORD;
  // No password configured → the tool is open; there is nothing to check and no
  // session to sign, just send them into the app.
  if (!expected) {
    return NextResponse.redirect(new URL("/knowledge", req.url), { status: 303 });
  }

  const form = await req.formData();
  const password = String(form.get("password") ?? "");
  if (password !== expected) {
    const url = new URL("/login?error=1", req.url);
    return NextResponse.redirect(url, { status: 303 });
  }

  const jar = await cookies();
  jar.set(session.cookieName, await signSession(), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: session.ttlSeconds,
  });

  return NextResponse.redirect(new URL("/knowledge", req.url), { status: 303 });
}
