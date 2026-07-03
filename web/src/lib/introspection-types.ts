// The chat-api's GET /v1/introspection wire shape, mirrored from
// `magi.agent.introspect.TeamSnapshot`. Hand-written (not codegen'd like
// api-types.ts) because it's the one read-only endpoint the BFF reads off the
// chat-api rather than the admin-api — keep it in sync with that Pydantic model.

export interface ToolInfo {
  name: string;
  description: string;
  instructions: string;
  /** "function" | "toolkit:<name>" | "mcp:<server>" */
  source: string;
}

export interface McpServerInfo {
  name: string;
  transport: string;
  url: string;
  connected: boolean;
  tools: string[];
  member: string;
}

export interface MemberInfo {
  name: string;
  role: string;
  model: string;
  tools: ToolInfo[];
}

export interface TeamSnapshot {
  name: string;
  lead_model: string;
  is_team: boolean;
  members: MemberInfo[];
  team_tools: ToolInfo[];
  mcp_servers: McpServerInfo[];
}
