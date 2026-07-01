// One session's machine-managed state: the live turn window, the rolling summary,
// and the pending buffer. Editable as raw files (validate-on-save).

import Link from "next/link";

import { Nav } from "@/components/Nav";
import { RawFileEditor } from "@/components/RawFileEditor";
import { getRawFile } from "@/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function SessionPage({
  params,
}: {
  params: Promise<{ user: string; sid: string }>;
}) {
  const { user, sid } = await params;
  const userId = decodeURIComponent(user);
  const sessionId = decodeURIComponent(sid);

  const kinds = [
    { kind: "session_window", label: "Live window (JSON list of turns)" },
    { kind: "session_summary", label: "Rolling summary (markdown)" },
    { kind: "session_pending", label: "Pending buffer (JSON list of turns)" },
  ];

  let files: Awaited<ReturnType<typeof getRawFile>>[] | null = null;
  let error: string | null = null;
  try {
    files = await Promise.all(
      kinds.map((k) => getRawFile(k.kind, { userId, sessionId })),
    );
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
      ) : (
        files?.map((file, i) => (
          <RawFileEditor
            key={kinds[i].kind}
            kind={kinds[i].kind}
            label={kinds[i].label}
            userId={userId}
            sessionId={sessionId}
            initialContent={file.content}
            initialVersion={file.version}
          />
        ))
      )}
    </main>
  );
}
