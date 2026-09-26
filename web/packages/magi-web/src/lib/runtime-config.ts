// Server-only runtime configuration store. Historically the BFF read all of its
// wiring — backend URLs, bearer tokens, storage locations — straight from
// process.env, so every change needed a redeploy/restart. This module layers a
// small JSON file on top: an operator can edit these from the Settings page and
// the change is persisted to disk. Resolution order for every key is
//
//     file override  →  environment variable  →  built-in default
//
// so an untouched deployment behaves exactly as before (env still seeds it) and
// the file only ever holds the keys someone deliberately changed.
//
// Secrets (bearer tokens) are stored here too but NEVER echoed back to the
// browser — GET reports only whether a value is set and where it came from.
// Bootstrap secrets that gate the login protecting this page (SESSION_SECRET,
// ADMIN_PASSWORD) are deliberately NOT managed here; they stay env-only.
//
// Import only from server components / route handlers — it touches the filesystem.

import "server-only";

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import {
  type ApplyMode,
  type ConfigFieldState,
  ConfigFileSchema,
  type ConfigGroup,
  type ConfigKey,
  ConfigKeySchema,
  type ConfigPatch,
  type ConfigSource,
  type ConfigState,
} from "./runtime-config-schema";

export {
  type ApplyMode,
  type ConfigFieldState,
  type ConfigGroup,
  type ConfigKey,
  type ConfigPatch,
  ConfigPatchSchema,
  type ConfigSource,
  type ConfigState,
} from "./runtime-config-schema";

export interface ConfigField {
  key: ConfigKey;
  /** The environment variable that seeds / falls back for this key. */
  env: string;
  /** Built-in default used when neither the file nor the env sets it. */
  fallback: string;
  label: string;
  group: ConfigGroup;
  apply: ApplyMode;
  /** A credential: its resolved value is never sent to the browser. */
  secret?: boolean;
  help?: string;
}

// The registry of runtime-editable settings. Anything not listed here (session
// secret, admin password, S3 credentials) stays env-only by design.
export const CONFIG_FIELDS: ConfigField[] = [
  {
    key: "adminApiUrl",
    env: "ADMIN_API_URL",
    fallback: "http://127.0.0.1:8000",
    label: "Admin API URL",
    group: "connection",
    apply: "live",
    help: "Base URL the BFF proxies admin routes to. The chat-api mounts the admin surface at /admin/v1/* on its own port, so this defaults to the chat-api URL.",
  },
  {
    key: "adminAuthToken",
    env: "ADMIN_AUTH_TOKEN",
    fallback: "",
    label: "Admin API token",
    group: "connection",
    apply: "live",
    secret: true,
    help: "Bearer token presented to the admin-api. Leave blank to use the environment value.",
  },
  {
    key: "chatApiUrl",
    env: "CHAT_API_URL",
    fallback: "http://127.0.0.1:8000",
    label: "Chat API URL",
    group: "connection",
    apply: "live",
    help: "Base URL of the Python chat-api (the running assistant).",
  },
  {
    key: "apiAuthToken",
    env: "API_AUTH_TOKEN",
    fallback: "",
    label: "Chat API token",
    group: "connection",
    apply: "live",
    secret: true,
    help: "Bearer token presented to the chat-api. Leave blank to use the environment value.",
  },
  {
    key: "primaryUser",
    env: "ALYSSA_PRIMARY_USER",
    fallback: "claudio",
    label: "Primary user id",
    group: "app",
    apply: "live",
    help: "The user id the companion home chats as; durable memory accrues to this person.",
  },
  {
    key: "chatHistoryDir",
    env: "CHAT_HISTORY_DIR",
    fallback: "",
    label: "Chat history directory",
    group: "app",
    apply: "restart",
    help: "Where chat transcripts are stored on disk. Blank = the OS temp dir (wiped on reboot).",
  },
  {
    key: "chatBlobDir",
    env: "CHAT_BLOB_DIR",
    fallback: "",
    label: "Chat blob directory",
    group: "app",
    apply: "restart",
    help: "Where inline chat media is stored on disk. Blank = the OS temp dir. Ignored when S3 blob storage is configured.",
  },
];

const FIELD_BY_KEY = Object.fromEntries(CONFIG_FIELDS.map((f) => [f.key, f])) as Record<
  ConfigKey,
  ConfigField
>;

function configFile(): string {
  // Sits next to the other durable web data (see CHAT_HISTORY_DIR). Override the
  // location with WEB_CONFIG_FILE.
  return path.resolve(process.env.WEB_CONFIG_FILE ?? "../data/web-config.json");
}

// Overrides parsed from disk, cached in-process. Loaded lazily on first read and
// refreshed on every save, so `live` keys reflect edits without a restart (the
// BFF is a single process, so this cache is authoritative).
let cache: Partial<Record<ConfigKey, string>> | null = null;

function load(): Partial<Record<ConfigKey, string>> {
  if (cache) return cache;
  try {
    const parsed = ConfigFileSchema.parse(JSON.parse(readFileSync(configFile(), "utf8")));
    const clean: Partial<Record<ConfigKey, string>> = {};
    for (const f of CONFIG_FIELDS) {
      const v = parsed[f.key];
      if (typeof v === "string") clean[f.key] = v;
    }
    cache = clean;
  } catch {
    cache = {}; // no file yet, or unreadable — treated as "no overrides"
  }
  return cache;
}

/** The resolved value for a key: file override → env → built-in default. Empty
 * strings never count as "set", so a blank override transparently falls back. */
export function getConfigValue(key: ConfigKey): string {
  const field = FIELD_BY_KEY[key];
  const override = load()[key];
  if (override) return override;
  const env = process.env[field.env];
  if (env) return env;
  return field.fallback;
}

function sourceOf(key: ConfigKey): ConfigSource {
  const field = FIELD_BY_KEY[key];
  if (load()[key]) return "file";
  if (process.env[field.env]) return "env";
  return "default";
}

export function readConfigState(): ConfigState {
  const overrides = load();
  const fields = CONFIG_FIELDS.map<ConfigFieldState>((f) => {
    const value = getConfigValue(f.key);
    const state: ConfigFieldState = {
      key: f.key,
      label: f.label,
      group: f.group,
      apply: f.apply,
      env: f.env,
      secret: !!f.secret,
      help: f.help,
      source: sourceOf(f.key),
      overridden: overrides[f.key] !== undefined,
      isSet: value !== "",
    };
    if (!f.secret) state.value = value;
    return state;
  });
  return { fields };
}

/** Apply a patch to the on-disk overrides, refresh the in-process cache, and
 * return the fresh browser-safe state. Rejects unknown keys. */
export function writeConfig(patch: ConfigPatch): ConfigState {
  const overrides: Partial<Record<ConfigKey, string>> = { ...load() };

  for (const [k, v] of Object.entries(patch.set ?? {})) {
    const key = ConfigKeySchema.safeParse(k);
    if (!key.success) throw new Error(`unknown config key: ${k}`);
    if (v.trim() !== "") overrides[key.data] = v;
  }
  for (const k of patch.clear ?? []) {
    const key = ConfigKeySchema.safeParse(k);
    if (!key.success) throw new Error(`unknown config key: ${k}`);
    delete overrides[key.data];
  }

  const file = configFile();
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(overrides, null, 2)}\n`, "utf8");
  cache = overrides;
  return readConfigState();
}
