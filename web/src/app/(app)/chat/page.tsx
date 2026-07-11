import { PageHeader, StatusBadge, StatusMessage, SurfacePanel } from "@carneirofc/ui";

import { ChatConsole } from "@carneirofc/magi-web/slices/chat/components";
import { chatCopy } from "@carneirofc/magi-web/slices/chat/screens";
import { getChatHealth } from "@carneirofc/magi-web/lib/chat-api";

export const dynamic = "force-dynamic";

export default async function ChatPage() {
  const health = await getChatHealth();

  return (
    <div className="app-page--fill flex flex-col gap-6">
      <PageHeader
        subtitle={chatCopy.subtitle}
        title={`${chatCopy.title} (custom composition)`}
        description="The app now owns the screen composition while reusing the stable Chat slice building blocks."
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

      <SurfacePanel tone="soft" padding="md" className="text-ui-sm text-[color:var(--ui-ink-muted)]">
        This page is app-owned: it keeps route ownership and page-level policy here while reusing
        the library's stable Chat slice exports.
      </SurfacePanel>

      {health ? (
        <ChatConsole />
      ) : (
        <StatusMessage role="alert" tone="error">
          Could not reach the chat API. Check CHAT_API_URL and API_AUTH_TOKEN, and that
          `python main.py api` is running.
        </StatusMessage>
      )}
    </div>
  );
}
