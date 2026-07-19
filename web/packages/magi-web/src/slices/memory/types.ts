import type {
  listUsers,
  getProfile,
  listSessions,
  getSession,
  getRawFile,
} from "../../lib/admin-api";

export type MemoryUser = Awaited<ReturnType<typeof listUsers>>["users"][number];
export type MemoryProfile = Awaited<ReturnType<typeof getProfile>>;
export type MemorySessionId = Awaited<ReturnType<typeof listSessions>>["sessions"][number];
export type MemorySession = Awaited<ReturnType<typeof getSession>>;
export type RawMemoryFile = Awaited<ReturnType<typeof getRawFile>>;

export type {
  MemoryTriggerAction,
  MemoryTriggerResult,
  RecallPreview,
  FileHistoryEntry,
  AdminMemorySettings,
} from "../../lib/admin-api";
