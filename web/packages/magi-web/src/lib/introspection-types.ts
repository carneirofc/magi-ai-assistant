// The chat-api's GET /v1/introspection wire shape, mirrored from
// `magi.agent.introspect.TeamSnapshot`. Hand-written (not codegen'd like
// api-types.ts) because it's the one read-only endpoint the BFF reads off the
// chat-api rather than the admin-api — keep the schemas in `wire-schemas.ts` in
// sync with that Pydantic model; these types derive from them.

import type { z } from "zod";

import type {
  McpServerInfoSchema,
  MemberInfoSchema,
  TeamSnapshotSchema,
  ToolInfoSchema,
} from "./wire-schemas";

export type ToolInfo = z.infer<typeof ToolInfoSchema>;
export type McpServerInfo = z.infer<typeof McpServerInfoSchema>;
export type MemberInfo = z.infer<typeof MemberInfoSchema>;
export type TeamSnapshot = z.infer<typeof TeamSnapshotSchema>;
