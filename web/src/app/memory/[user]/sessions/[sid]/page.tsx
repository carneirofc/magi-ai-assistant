// One session's machine-managed state: the live turn window, the rolling summary,
// and the pending (not-yet-summarized) buffer. Read-only.

import Link from "next/link";

import { Nav } from "@/components/Nav";
import { getSession } from "@/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function SessionPage({
  params,
}: {
  params: Promise<{ user: string; sid: string }>;
}) {
  const { user, sid } = await params;
  const userId = decodeURIComponent(user);
  const sessionId = decodeURIComponent(sid);

  let detail: Awaited<ReturnType<typeof getSession>> | null = null;
  let error: string | null = null;
  try {
    detail = await getSession(userId, sessionId);
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <main>
      <Nav title={`Session · ${sessionId}`} />
      <p>
        <Link href={`/memory/${encodeURIComponent(userId)}`}>← {userId}</Link>
      </p>

      {error ? (
        <p className="error">{error}</p>
      ) : detail ? (
        <>
          <section>
            <h2>Live window</h2>
            {detail.turns.length > 0 ? (
              <ul>
                {detail.turns.map((t, i) => (
                  <li key={i}>
                    <strong>{t.role}:</strong> {t.content}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">Empty.</p>
            )}
          </section>

          <section>
            <h2>Rolling summary</h2>
            <p className={detail.summary ? "" : "muted"}>
              {detail.summary || "None."}
            </p>
          </section>

          {detail.pending.length > 0 ? (
            <section>
              <h2>Pending (awaiting summary)</h2>
              <ul>
                {detail.pending.map((t, i) => (
                  <li key={i}>
                    <strong>{t.role}:</strong> {t.content}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}
    </main>
  );
}
