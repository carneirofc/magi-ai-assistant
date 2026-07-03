// One session's machine-managed state: the live turn window, the rolling summary,
// and the pending buffer. Editable as raw files (validate-on-save).

import Link from "next/link";
import { PageHeader, StatusMessage, SurfacePanel } from "@carneirofc/ui";

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
    {
      kind: "session_window",
      label: "Live window",
      description: "JSON list of the most recent turns.",
    },
    {
      kind: "session_summary",
      label: "Rolling summary",
      description: "Markdown summary of turns that rolled out of the window.",
    },
    {
      kind: "session_pending",
      label: "Pending buffer",
      description: "JSON list of turns awaiting the next fold.",
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
        description={sessionId}
      />

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : (
        <div className="flex flex-col gap-4">
          {files?.map((file, i) => (
            <SurfacePanel key={kinds[i].kind} tone="soft" padding="lg">
              <RawFileEditor
                kind={kinds[i].kind}
                label={kinds[i].label}
                description={kinds[i].description}
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
