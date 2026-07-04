// Subjects management — the controlled vocabulary behind knowledge grouping.

import { PageHeader, StatusMessage } from "@carneirofc/ui";

import { SubjectManager } from "@carneirofc/magi-web/components/SubjectManager";
import { listSubjects } from "@carneirofc/magi-web/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function SubjectsPage() {
  let subjects: Awaited<ReturnType<typeof listSubjects>>["subjects"] = [];
  let error: string | null = null;
  try {
    subjects = (await listSubjects()).subjects;
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        subtitle="magi // knowledge"
        title="Subjects"
        description="The controlled vocabulary documents are grouped by. A hard filter on the assistant's knowledge search."
      />
      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : (
        <SubjectManager subjects={subjects} />
      )}
    </div>
  );
}
