// Memory: the user list as searchable cards. Each links to that user's profile.

import { EmptyState, PageHeader, StatusMessage } from "@carneirofc/ui";

import { UserGrid } from "../components/UserGrid";
import { listUsers } from "../lib/admin-api";
import { mergeCopy, type PageCopy } from "../lib/page-copy";

export const memoryCopy = {
  subtitle: "magi // memory",
  title: "Memory",
  description:
    "Every user the assistant keeps durable memory for — curated facts, recorded episodes, and live sessions.",
} as const;

export async function MemoryView({ copy }: { copy?: PageCopy } = {}) {
  const header = mergeCopy(memoryCopy, copy);

  let users: Awaited<ReturnType<typeof listUsers>>["users"] = [];
  let error: string | null = null;
  try {
    users = (await listUsers()).users;
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader subtitle={header.subtitle} title={header.title} description={header.description} />

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : users.length === 0 ? (
        <EmptyState>No users with memory yet.</EmptyState>
      ) : (
        <UserGrid users={users} />
      )}
    </div>
  );
}

export default function MemoryPage() {
  return <MemoryView />;
}
