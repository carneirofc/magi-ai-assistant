// Zod schemas for the JSON shapes the admin-api OpenAPI does NOT cover: the
// chat-api's responses, this BFF's own route bodies, and browser storage. Client-
// safe (no server-only imports) so components can validate BFF responses too.
// Admin-api shapes come from the generated `api-schemas.ts` instead.
//
// Schemas that mirror an existing exported interface are typed
// `z.ZodType<Interface>`, so the two cannot drift apart silently.

import type { ThreadMessageLike } from "@assistant-ui/react";
import { z } from "zod";

import type { ArchiveHit, BotIdentity, ContextStats, SelfMemoryFact } from "./chat-api";

/** A JSON object with unknown fields — the lower bound for any parsed body. */
export const JsonObjectSchema = z.record(z.string(), z.unknown());

/** An error body (`{ error }`) from a BFF route or upstream. */
export const ErrorBodySchema = z.object({ error: z.string().optional() });

/** A write that returns the new optimistic-concurrency token. */
export const VersionBodySchema = z.object({ version: z.string() });

// --- chat-api -----------------------------------------------------------------
export const BotIdentitySchema: z.ZodType<BotIdentity> = z.object({
  display_name: z.string(),
  description: z.string(),
  has_avatar: z.boolean(),
  avatar_mime: z.string().nullable(),
  version: z.string(),
  moods: z.array(z.string()).optional(),
  mood_vocab_version: z.number().optional(),
  expressions: z.record(z.string(), z.object({ mime: z.string(), version: z.string() })).optional(),
  tts_enabled: z.boolean().optional(),
  stt_enabled: z.boolean().optional(),
});

export const ContextStatsSchema: z.ZodType<ContextStats> = z.object({
  total_chars: z.number(),
  est_tokens: z.number(),
  token_source: z.string().optional(),
  budget_tokens: z.number(),
  warn_ratio: z.number().optional(),
  ratio: z.number(),
  sections: z.record(z.string(), z.number()),
  section_budgets: z.record(z.string(), z.number()).optional(),
  short_term_turns: z.number(),
  knowledge: z
    .object({ auto_inject: z.boolean(), top_k: z.number(), est_max_tokens: z.number() })
    .optional(),
});

export const ArchiveHitSchema: z.ZodType<ArchiveHit> = z.object({
  kind: z.string(),
  session_id: z.string().nullable(),
  role: z.string().nullable(),
  ts: z.string().nullable(),
  snippet: z.string(),
});
export const ArchiveHitsSchema = z.object({ hits: z.array(ArchiveHitSchema).catch([]) });

export const SelfMemoryFactSchema: z.ZodType<SelfMemoryFact> = z.object({
  text: z.string(),
  ts: z.string(),
});
export const SelfMemoryFactsSchema = z.object({
  facts: z.array(SelfMemoryFactSchema).catch([]),
});

export const TitleBodySchema = z.object({ title: z.string().nullish() });
export const FlushBodySchema = z.object({ dropped_turns: z.number().catch(0) });
export const TranscriptBodySchema = z.object({ text: z.string().optional() });

export const ReminderSchema = z.object({
  id: z.string(),
  text: z.string(),
  due: z.string(),
  done: z.boolean().optional(),
});
export type Reminder = z.infer<typeof ReminderSchema>;
export const RemindersBodySchema = z.object({ reminders: z.array(ReminderSchema).catch([]) });

// --- introspection (GET /v1/introspection) -------------------------------------
export const ToolInfoSchema = z.object({
  name: z.string(),
  description: z.string(),
  instructions: z.string(),
  /** "function" | "toolkit:<name>" | "mcp:<server>" */
  source: z.string(),
  /** "builtin" | "recipe" | "registered" | "skill" | "mcp"; absent reads as "builtin". */
  origin: z.string().optional(),
});

export const McpServerInfoSchema = z.object({
  name: z.string(),
  transport: z.string(),
  url: z.string(),
  connected: z.boolean(),
  tools: z.array(z.string()),
  member: z.string(),
});

export const MemberInfoSchema = z.object({
  name: z.string(),
  role: z.string(),
  model: z.string(),
  tools: z.array(ToolInfoSchema),
});

export const TeamSnapshotSchema = z.object({
  name: z.string(),
  lead_model: z.string(),
  is_team: z.boolean(),
  members: z.array(MemberInfoSchema),
  team_tools: z.array(ToolInfoSchema),
  mcp_servers: z.array(McpServerInfoSchema),
});

// --- chat transcript store ------------------------------------------------------
/** assistant-ui's lenient message shape; its own importer normalizes the rest,
 * so only "is an object" is checked here. */
export const ThreadMessageLikeSchema = z.custom<ThreadMessageLike>(
  (v) => typeof v === "object" && v !== null,
);

export const StoredThreadSchema = z.object({
  headId: z.string().nullish(),
  items: z
    .array(z.object({ message: ThreadMessageLikeSchema, parentId: z.string().nullish() }))
    .catch([]),
});

/** The transcript as the search/export paths read it: every field optional. */
export const LooseThreadSchema = z.object({
  headId: z.string().nullish(),
  items: z
    .array(
      z.object({
        message: z
          .object({
            id: z.string().optional(),
            role: z.string().optional(),
            content: z.unknown(),
            createdAt: z.string().optional(),
          })
          .optional(),
        parentId: z.string().nullish(),
      }),
    )
    .catch([]),
});

// --- browser storage ------------------------------------------------------------
export const ChatSessionSchema = z.object({
  id: z.string(),
  title: z.string(),
  createdAt: z.number(),
  updatedAt: z.number(),
  pinned: z.boolean().optional(),
  archived: z.boolean().optional(),
});

export const SessionRegistrySchema = z.object({
  activeId: z.string().optional(),
  sessions: z.array(ChatSessionSchema),
});
