// Subjects management — the controlled vocabulary behind knowledge grouping.

import { PageHeader, StatusMessage } from "@carneirofc/ui";

import { AppPage } from "../components/AppPage";
import { ScrollRegion } from "../components/ScrollRegion";
import { SubjectManager } from "../components/SubjectManager";
import { listSubjects } from "../lib/admin-api";
import { mergeCopy, type PageCopy } from "../lib/page-copy";

export const subjectsCopy = {
  subtitle: "magi // knowledge",
  title: "Subjects",
  description:
    "The controlled vocabulary documents are grouped by. A hard filter on the assistant's knowledge search.",
} as const;

export async function SubjectsView({ copy }: { copy?: PageCopy } = {}) {
  const header = mergeCopy(subjectsCopy, copy);

  let subjects: Awaited<ReturnType<typeof listSubjects>>["subjects"] = [];
  let error: string | null = null;
  try {
    subjects = (await listSubjects()).subjects;
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <AppPage className="gap-6">
      <PageHeader subtitle={header.subtitle} title={header.title} description={header.description} />
      <ScrollRegion className="flex flex-col gap-6">
        {error ? (
          <StatusMessage role="alert" tone="error">
            {error}
          </StatusMessage>
        ) : (
          <SubjectManager subjects={subjects} />
        )}
      </ScrollRegion>
    </AppPage>
  );
}

export default function SubjectsPage() {
  return <SubjectsView />;
}
