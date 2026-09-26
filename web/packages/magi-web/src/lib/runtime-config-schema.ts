// Zod schemas for the runtime-config wire shapes. Kept apart from
// runtime-config.ts (server-only, touches the filesystem) so client components
// can validate the settings API responses without importing server code.

import { z } from "zod";

export const ConfigKeySchema = z.enum([
  "adminApiUrl",
  "adminAuthToken",
  "chatApiUrl",
  "apiAuthToken",
  "primaryUser",
  "chatHistoryDir",
  "chatBlobDir",
]);
export type ConfigKey = z.infer<typeof ConfigKeySchema>;

export const ConfigGroupSchema = z.enum(["connection", "app"]);
export type ConfigGroup = z.infer<typeof ConfigGroupSchema>;

/** `live` keys are re-read per request (edits apply immediately); `restart` keys
 * are read once at startup, so a saved change only takes effect after a restart —
 * the editor flags these so the operator knows. */
export const ApplyModeSchema = z.enum(["live", "restart"]);
export type ApplyMode = z.infer<typeof ApplyModeSchema>;

export const ConfigSourceSchema = z.enum(["file", "env", "default"]);
export type ConfigSource = z.infer<typeof ConfigSourceSchema>;

/** A browser-safe view of one field: the resolved value for non-secrets, or just
 * whether one is set for secrets — the token bytes never leave the server. */
export const ConfigFieldStateSchema = z.object({
  key: ConfigKeySchema,
  label: z.string(),
  group: ConfigGroupSchema,
  apply: ApplyModeSchema,
  env: z.string(),
  secret: z.boolean(),
  help: z.string().optional(),
  source: ConfigSourceSchema,
  /** A file override exists (so it can be reset back to env/default). */
  overridden: z.boolean(),
  /** Whether any value resolves (used for secrets, where `value` is withheld). */
  isSet: z.boolean(),
  /** The resolved value — omitted for secrets. */
  value: z.string().optional(),
});
export type ConfigFieldState = z.infer<typeof ConfigFieldStateSchema>;

export const ConfigStateSchema = z.object({ fields: z.array(ConfigFieldStateSchema) });
export type ConfigState = z.infer<typeof ConfigStateSchema>;

/** A settings PUT body. Keys are checked against the registry in `writeConfig`
 * (so an unknown key is a 400 with a clear message, not a schema error). */
export const ConfigPatchSchema = z.object({
  /** Upsert these overrides. Empty / blank values are ignored — use `clear`. */
  set: z.record(z.string(), z.string()).optional(),
  /** Remove these overrides so the key falls back to env/default. */
  clear: z.array(z.string()).optional(),
});
export type ConfigPatch = z.infer<typeof ConfigPatchSchema>;

/** The on-disk override file: any string values (unknown keys are dropped on load). */
export const ConfigFileSchema = z.record(z.string(), z.unknown());
