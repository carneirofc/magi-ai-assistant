// Memory: the user list as searchable cards. Each links to that user's profile.

import { EmptyState, PageHeader, StatusMessage } from "@carneirofc/ui";

import { UserGrid } from "@carneirofc/magi-web/components/UserGrid";
import { listUsers } from "@carneirofc/magi-web/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function MemoryPage() {
  let users: Awaited<ReturnType<typeof listUsers>>["users"] = [];
  let error: string | null = null;
  try {
    users = (await listUsers()).users;
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        subtitle="magi // memory"
        title="Memory"
        description="Every user the assistant keeps durable memory for — curated facts, recorded episodes, and live sessions."
      />

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
