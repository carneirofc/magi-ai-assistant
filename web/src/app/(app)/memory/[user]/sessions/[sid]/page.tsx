// One session's machine-managed state: the live turn window, the rolling summary,
// and the pending buffer. Window/pending render as a chat transcript (Rendered)
// or raw JSON (Raw); the summary renders as prose or raw markdown.

import Link from "next/link";
import { PageHeader, StatusMessage, SurfacePanel } from "@carneirofc/ui";

import { CopyId } from "@/components/CopyId";
import { SessionFile } from "@/components/SessionFile";
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
    {
      kind: "session_window",
      label: "Live window",
      description: "The most recent turns kept verbatim.",
      render: "turns" as const,
    },
    {
      kind: "session_summary",
      label: "Rolling summary",
      description: "Summary of turns that rolled out of the window.",
      render: "text" as const,
    },
    {
      kind: "session_pending",
      label: "Pending buffer",
      description: "Turns awaiting the next fold.",
      render: "turns" as const,
    },
  ];

  let files: Awaited<ReturnType<typeof getRawFile>>[] | null = null;
  let error: string | null = null;
  try {
    files = await Promise.all(kinds.map((k) => getRawFile(k.kind, { userId, sessionId })));
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link href={`/memory/${encodeURIComponent(userId)}`} className="text-ui-xs">
          ← {userId}
        </Link>
      </div>
      <PageHeader
        subtitle={`magi // memory // ${userId}`}
        title="Session"
        description="Machine-managed conversation state — the live window, rolling summary, and pending buffer."
      />
      <CopyId value={sessionId} className="self-start" />

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : (
        <div className="flex flex-col gap-4">
          {files?.map((file, i) => (
            <SurfacePanel key={kinds[i].kind} tone="soft" padding="lg">
              <SessionFile
                kind={kinds[i].kind}
                label={kinds[i].label}
                description={kinds[i].description}
                render={kinds[i].render}
                userId={userId}
                sessionId={sessionId}
                initialContent={file.content}
                initialVersion={file.version}
              />
            </SurfacePanel>
          ))}
        </div>
      )}
    </div>
  );
}
