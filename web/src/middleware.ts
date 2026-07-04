// Auth gate for the whole app. The verification logic lives in the library
// (@carneirofc/magi-web/middleware); this file mounts it and owns the matcher —
// which paths Next actually runs middleware on (policy). Next reads `config`
// statically from the middleware file, so it stays a local literal here.

export { middleware } from "@carneirofc/magi-web/middleware";

export const config = {
  // Run on everything except Next internals and static assets.
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
