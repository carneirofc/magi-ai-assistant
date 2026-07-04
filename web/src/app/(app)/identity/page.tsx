// The global bot identity — the name, description, and profile picture the bot
// presents as itself. One shared profile, injected into every conversation and
// shown as the assistant's face in chat.

import { PageHeader, StatusMessage, SurfacePanel } from "@carneirofc/ui";

import { IdentityEditor } from "@carneirofc/magi-web/components/IdentityEditor";
import { getIdentity } from "@carneirofc/magi-web/lib/admin-api";

export const dynamic = "force-dynamic";

export default async function IdentityPage() {
  let identity: Awaited<ReturnType<typeof getIdentity>> | null = null;
  let error: string | null = null;
  try {
    identity = await getIdentity();
  } catch {
    error = "Could not reach the admin API.";
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        subtitle="magi // identity"
        title="Identity"
        description="The bot's name, description, and profile picture — how it presents itself. Injected into every conversation (the model reads it and, when enabled, sees the picture) and shown as the assistant's face in chat."
      />

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : identity ? (
        <SurfacePanel tone="soft" padding="lg">
          <IdentityEditor initial={identity} />
        </SurfacePanel>
      ) : null}
    </div>
  );
}
