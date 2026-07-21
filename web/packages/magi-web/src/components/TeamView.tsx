// Read-only render of the live team snapshot from the chat-api: the lead model,
// each specialist member with its role/model/tools, the team-level tools, and any
// MCP servers with their connection status. Pure/presentational (server component)
// — no interactivity, no client state.

import {
  EmptyState,
  InfoChip,
  StatusBadge,
  SurfacePanel,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@carneirofc/ui";

import type { McpServerInfo, TeamSnapshot, ToolInfo } from "../lib/introspection-types";

function ToolTable({ tools }: { tools: ToolInfo[] }) {
  if (tools.length === 0) {
    return <EmptyState>No tools.</EmptyState>;
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Tool</TableHead>
          <TableHead>Description</TableHead>
          <TableHead>Source</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {tools.map((t) => (
          <TableRow key={`${t.source}:${t.name}`}>
            <TableCell className="font-mono text-ui-xs">{t.name}</TableCell>
            <TableCell>
              {t.description ? (
                <span>{t.description}</span>
              ) : (
                <span className="text-[color:var(--ui-ink-subtle)]">—</span>
              )}
              {t.instructions ? (
                <span className="mt-0.5 block text-ui-2xs text-[color:var(--ui-ink-subtle)]">
                  {t.instructions}
                </span>
              ) : null}
            </TableCell>
            <TableCell>
              <span className="text-ui-2xs text-[color:var(--ui-ink-subtle)]">{t.source}</span>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// The capability roster's grouping: how each lead tool got here. Order is
// trust-descending — engine first, then operator/persona-granted growth.
const ORIGIN_GROUPS: Array<{ origin: string; label: string }> = [
  { origin: "builtin", label: "Built-in" },
  { origin: "skill", label: "Skills" },
  { origin: "registered", label: "Registered (persona)" },
  { origin: "recipe", label: "Approved recipes" },
  { origin: "mcp", label: "MCP" },
];

function GroupedToolTables({ tools }: { tools: ToolInfo[] }) {
  if (tools.length === 0) {
    return <EmptyState>No tools.</EmptyState>;
  }
  const known = new Set(ORIGIN_GROUPS.map((g) => g.origin));
  const groups = ORIGIN_GROUPS.map((g) => ({
    ...g,
    tools: tools.filter((t) => (t.origin ?? "builtin") === g.origin),
  })).filter((g) => g.tools.length > 0);
  // Anything with an unknown origin still shows — never silently dropped.
  const other = tools.filter((t) => t.origin && !known.has(t.origin));
  return (
    <div className="flex flex-col gap-4">
      {groups.map((g) => (
        <div key={g.origin} className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <h3 className="text-ui-sm font-semibold">{g.label}</h3>
            <span className="text-ui-2xs text-[color:var(--ui-ink-subtle)]">
              {g.tools.length}
            </span>
          </div>
          <ToolTable tools={g.tools} />
        </div>
      ))}
      {other.length > 0 ? (
        <div className="flex flex-col gap-2">
          <h3 className="text-ui-sm font-semibold">Other</h3>
          <ToolTable tools={other} />
        </div>
      ) : null}
    </div>
  );
}

function McpCard({ server }: { server: McpServerInfo }) {
  return (
    <SurfacePanel tone="subtle" padding="md" className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-ui-md font-semibold">{server.name}</span>
          {server.member ? <InfoChip>{server.member}</InfoChip> : null}
        </div>
        <StatusBadge tone={server.connected ? "success" : "error"}>
          <span
            className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full"
            style={{ background: "currentColor" }}
          />
          {server.connected ? "Connected" : "Disconnected"}
        </StatusBadge>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-ui-xs">
        {server.transport ? (
          <>
            <dt className="text-[color:var(--ui-ink-subtle)]">Transport</dt>
            <dd className="font-mono">{server.transport}</dd>
          </>
        ) : null}
        {server.url ? (
          <>
            <dt className="text-[color:var(--ui-ink-subtle)]">URL</dt>
            <dd className="font-mono break-all">{server.url}</dd>
          </>
        ) : null}
      </dl>
      {server.tools.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {server.tools.map((name) => (
            <InfoChip key={name}>
              <span className="font-mono">{name}</span>
            </InfoChip>
          ))}
        </div>
      ) : (
        <span className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
          No tools discovered yet.
        </span>
      )}
    </SurfacePanel>
  );
}

export function TeamView({ snapshot }: { snapshot: TeamSnapshot }) {
  return (
    <div className="flex flex-col gap-6">
      {/* Members */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-ui-lg font-semibold">
            {snapshot.is_team ? "Members" : "Agent"}
          </h2>
          <span className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
            {snapshot.members.length} specialist{snapshot.members.length === 1 ? "" : "s"}
          </span>
        </div>
        {snapshot.members.length === 0 ? (
          <SurfacePanel tone="soft" padding="lg">
            <EmptyState>
              This runner has no specialist members — it&apos;s a single agent. Its tools are
              listed below.
            </EmptyState>
          </SurfacePanel>
        ) : (
          snapshot.members.map((m) => (
            <SurfacePanel key={m.name} tone="soft" padding="lg" className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <h3 className="text-ui-md font-semibold">{m.name}</h3>
                  {m.model ? (
                    <InfoChip>
                      <span className="font-mono">{m.model}</span>
                    </InfoChip>
                  ) : null}
                </div>
                <span className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
                  {m.tools.length} tool{m.tools.length === 1 ? "" : "s"}
                </span>
              </div>
              {m.role ? (
                <p className="whitespace-pre-wrap text-ui-sm text-[color:var(--ui-ink-subtle)]">
                  {m.role}
                </p>
              ) : null}
              <ToolTable tools={m.tools} />
            </SurfacePanel>
          ))
        )}
      </section>

      {/* Team-level tools */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-ui-lg font-semibold">
            {snapshot.is_team ? "Lead tools" : "Tools"}
          </h2>
          <span className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
            {snapshot.team_tools.length} tool{snapshot.team_tools.length === 1 ? "" : "s"}
          </span>
        </div>
        <SurfacePanel tone="soft" padding="lg">
          <GroupedToolTables tools={snapshot.team_tools} />
        </SurfacePanel>
      </section>

      {/* MCP servers */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-ui-lg font-semibold">MCP servers</h2>
          <span className="text-ui-xs text-[color:var(--ui-ink-subtle)]">
            {snapshot.mcp_servers.length} connected member{snapshot.mcp_servers.length === 1 ? "" : "s"}
          </span>
        </div>
        {snapshot.mcp_servers.length === 0 ? (
          <SurfacePanel tone="soft" padding="lg">
            <EmptyState>
              No members use an MCP server in this process. (The Seanime member uses MCP only when
              seanime_use_mcp is on.)
            </EmptyState>
          </SurfacePanel>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {snapshot.mcp_servers.map((s) => (
              <McpCard key={`${s.member}:${s.name}`} server={s} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
