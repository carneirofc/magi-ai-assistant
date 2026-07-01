// Add knowledge — paste text or upload a file, assign a subject + tags.

import Link from "next/link";

import { AddKnowledge } from "@/components/AddKnowledge";
import { Nav } from "@/components/Nav";
import { listSubjects } from "@/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function AddKnowledgePage() {
  let subjects: { id: string; name: string }[] = [];
  let error: string | null = null;
  try {
    subjects = (await listSubjects()).subjects;
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <main>
      <Nav title="Add knowledge" />
      <p>
        <Link href="/knowledge">← all documents</Link>
      </p>
      {error ? <p className="error">{error}</p> : <AddKnowledge subjects={subjects} />}
    </main>
  );
}
