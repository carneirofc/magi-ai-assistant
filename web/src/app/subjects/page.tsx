// Subjects management — the controlled vocabulary behind knowledge grouping.

import { Nav } from "@/components/Nav";
import { SubjectManager } from "@/components/SubjectManager";
import { listSubjects } from "@/lib/admin-api";

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
    <main>
      <Nav title="Subjects" />
      {error ? <p className="error">{error}</p> : <SubjectManager subjects={subjects} />}
    </main>
  );
}
