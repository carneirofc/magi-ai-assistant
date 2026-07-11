// Companion surface demo — the persona visually present beside the chat. The
// stage's expression map is built from the identity API's expression pack
// (moods with an uploaded portrait resolve through the avatar BFF relay,
// version-keyed for cache busting); with no pack uploaded the stage falls back
// to the plain avatar as `neutral`, and with no avatar at all it renders its
// monogram placeholder. The app owns this composition; the library brings
// CompanionSurface + the chat slice.

import { PageHeader, StatusBadge, StatusMessage } from "@carneirofc/ui";

import { CompanionSurface, MemoryPanel } from "@carneirofc/magi-web/slices/companion/components";
import { ChatConsole } from "@carneirofc/magi-web/slices/chat/components";
import { getChatHealth, getIdentity } from "@carneirofc/magi-web/lib/chat-api";

export const dynamic = "force-dynamic";

export default async function CompanionPage() {
  const [health, identity] = await Promise.all([getChatHealth(), getIdentity()]);

  const expressions: Record<string, string> = {};
  for (const [mood, entry] of Object.entries(identity?.expressions ?? {})) {
    expressions[mood] = `/api/identity/avatar?mood=${encodeURIComponent(mood)}&v=${entry.version}`;
  }
  if (!expressions.neutral && identity?.has_avatar) {
    expressions.neutral = `/api/identity/avatar?v=${identity.version}`;
  }

  return (
    <div className="app-page--fill flex flex-col gap-6">
      <PageHeader
        subtitle="companion // demo"
        title="Companion"
        description="The assistant's face beside the transcript, reacting to the streamed mood."
        pills={
          <StatusBadge tone={health ? "success" : "error"}>
            <span
              className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full"
              style={{ background: "currentColor" }}
            />
            {health ? "Chat API online" : "Chat API offline"}
          </StatusBadge>
        }
      />

      {health ? (
        <CompanionSurface
          expressions={expressions}
          name={identity?.display_name || null}
          aside={<MemoryPanel userId="console" />}
        >
          <ChatConsole greetOnOpen />
        </CompanionSurface>
      ) : (
        <StatusMessage role="alert" tone="error">
          Could not reach the chat API. Check CHAT_API_URL and API_AUTH_TOKEN, and that
          `python main.py api` is running.
        </StatusMessage>
      )}
    </div>
  );
}
