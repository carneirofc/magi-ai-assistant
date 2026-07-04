// Team page: the live composition of the running brain — lead model, specialist
// members with their tools, and any MCP servers. Read-only. Unlike the other
// dashboard pages this reads the CHAT-API (channels/api.py), the process that
// actually assembles the team, not the admin-api.

import { PageHeader, StatusBadge, StatusMessage } from "@carneirofc/ui";

import { TeamView } from "@carneirofc/magi-web/components/TeamView";
import { getChatHealth, getIntrospection } from "@carneirofc/magi-web/lib/chat-api";
import type { TeamSnapshot } from "@carneirofc/magi-web/lib/introspection-types";

export const dynamic = "force-dynamic";

export default async function TeamPage() {
  let snapshot: TeamSnapshot | null = null;
  let error: string | null = null;
  const health = await getChatHealth();
  try {
    snapshot = await getIntrospection();
  } catch {
    error =
      "Could not reach the chat API. Check CHAT_API_URL and API_AUTH_TOKEN, and that `python main.py api` is running.";
  }

  const toolCount = snapshot
    ? snapshot.team_tools.length +
      snapshot.members.reduce((n, m) => n + m.tools.length, 0)
    : 0;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        subtitle="magi // team"
        title="Team"
        description="The live roster powering the assistant — the lead router, its specialist members and their tools, and any MCP servers they connect to."
        pills={
          <>
            <StatusBadge tone={health ? "success" : "error"}>
              <span
                className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full"
                style={{ background: "currentColor" }}
              />
              {health ? "Chat API online" : "Chat API offline"}
            </StatusBadge>
            {snapshot?.lead_model ? (
              <StatusBadge tone="neutral">
                <span className="font-mono">{snapshot.lead_model}</span>
              </StatusBadge>
            ) : null}
            {snapshot ? (
              <StatusBadge tone="neutral">{toolCount} tools</StatusBadge>
            ) : null}
          </>
        }
      />

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : snapshot ? (
        <TeamView snapshot={snapshot} />
      ) : null}
    </div>
  );
}
