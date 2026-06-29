// Top nav shared across admin pages. Server component; the sign-out posts to the
// logout route.

import Link from "next/link";

export function Nav({ title }: { title: string }) {
  return (
    <div className="topbar">
      <div style={{ display: "flex", gap: "1rem", alignItems: "baseline" }}>
        <strong>{title}</strong>
        <nav style={{ display: "flex", gap: "0.8rem" }}>
          <Link href="/knowledge">Knowledge</Link>
          <Link href="/subjects">Subjects</Link>
          <Link href="/memory">Memory</Link>
          <Link href="/persona">Persona</Link>
        </nav>
      </div>
      <form method="post" action="/api/auth/logout">
        <button className="ghost" type="submit">
          Sign out
        </button>
      </form>
    </div>
  );
}
