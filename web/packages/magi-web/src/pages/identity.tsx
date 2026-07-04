// The global bot identity — the name, description, and profile picture the bot
// presents as itself. One shared profile, injected into every conversation and
// shown as the assistant's face in chat.

import { PageHeader, StatusMessage, SurfacePanel } from "@carneirofc/ui";

import { IdentityEditor } from "../components/IdentityEditor";
import { getIdentity } from "../lib/admin-api";
import { mergeCopy, type PageCopy } from "../lib/page-copy";

export const identityCopy = {
  subtitle: "magi // identity",
  title: "Identity",
  description:
    "The assistant's name, description, and profile picture — how it presents itself. Injected into every conversation (the assistant reads it and, when enabled, sees the picture) and shown as its face in chat.",
} as const;

/** The Identity view. Pass `copy` to reskin the header; an overlay composes this
 * directly, the reference app mounts the default page below. */
export async function IdentityView({ copy }: { copy?: PageCopy } = {}) {
  const header = mergeCopy(identityCopy, copy);

  let identity: Awaited<ReturnType<typeof getIdentity>> | null = null;
  let error: string | null = null;
  try {
    identity = await getIdentity();
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
      ) : identity ? (
        <SurfacePanel tone="soft" padding="lg">
          <IdentityEditor initial={identity} />
        </SurfacePanel>
      ) : null}
    </div>
  );
}

export default function IdentityPage() {
  return <IdentityView />;
}
