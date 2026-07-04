// Chat page: an operator console for talking to the running brain. Like the Team
// page it targets the CHAT-API (channels/api.py) — the process that assembles the
// team — rather than the admin-api. The header shows a live online/offline badge;
// the streaming conversation itself lives in the ChatConsole client component.

import { PageHeader, StatusBadge, StatusMessage } from "@carneirofc/ui";

import { ChatConsole } from "@/components/ChatConsole";
import { getChatHealth } from "@/lib/chat-api";

export const dynamic = "force-dynamic";

export default async function ChatPage() {
  const health = await getChatHealth();

  return (
    <div className="app-page--fill flex flex-col gap-6">
      <PageHeader
        subtitle="magi // chat"
        title="Chat"
        description="Talk to the live team over a streaming connection. Handy for probing routing, tools, and what the brain remembers for a given user."
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
