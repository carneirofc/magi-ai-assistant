import { z } from "zod";

const scope = z.union([z.string(), z.null()]).optional();
const DocumentSummaryOut = z
  .object({
    doc_id: z.string(),
    source: z.string(),
    title: z.string(),
    subject: z.string(),
    tags: z.array(z.string()),
    scope: z.string(),
    chunk_count: z.number().int(),
    latest_ts: z.string(),
  })
  .passthrough();
const DocumentList = z
  .object({ documents: z.array(DocumentSummaryOut) })
  .passthrough();
const ValidationError = z
  .object({
    loc: z.array(z.union([z.string(), z.number()])),
    msg: z.string(),
    type: z.string(),
    input: z.unknown().optional(),
    ctx: z.object({}).partial().passthrough().optional(),
  })
  .passthrough();
const HTTPValidationError = z
  .object({ detail: z.array(ValidationError) })
  .partial()
  .passthrough();
const IngestDocument = z
  .object({
    title: z.string().min(1),
    text: z.string().min(1),
    doc_id: z.union([z.string(), z.null()]).optional(),
    subject: z.string().optional().default(""),
    tags: z.array(z.string()).optional(),
  })
  .passthrough();
const IngestResult = z
  .object({ doc_id: z.string(), chunks_indexed: z.number().int() })
  .passthrough();
const TagList = z.object({ tags: z.array(z.string()) }).passthrough();
const SetSubject = z.object({ subject: z.string() }).passthrough();
const ChunkOut = z
  .object({ chunk_index: z.number().int(), text: z.string() })
  .passthrough();
const DocumentDetailOut = z
  .object({
    doc_id: z.string(),
    source: z.string(),
    title: z.string(),
    subject: z.string(),
    tags: z.array(z.string()),
    scope: z.string(),
    chunks: z.array(ChunkOut),
  })
  .passthrough();
const EditTags = z
  .object({ add: z.array(z.string()), remove: z.array(z.string()) })
  .partial()
  .passthrough();
const RenameDocument = z.object({ title: z.string().min(1) }).passthrough();
const SubjectOut = z
  .object({
    id: z.string(),
    name: z.string(),
    description: z.string().optional().default(""),
  })
  .passthrough();
const SubjectListOut = z
  .object({ subjects: z.array(SubjectOut) })
  .passthrough();
const CreateSubject = z
  .object({
    name: z.string().min(1),
    description: z.string().optional().default(""),
  })
  .passthrough();
const EditSubject = z
  .object({
    name: z.union([z.string(), z.null()]),
    description: z.union([z.string(), z.null()]),
  })
  .partial()
  .passthrough();
const UserSummary = z
  .object({
    user_id: z.string(),
    fact_count: z.number().int(),
    episode_count: z.number().int(),
    session_count: z.number().int(),
  })
  .passthrough();
const UserList = z.object({ users: z.array(UserSummary) }).passthrough();
const Fact = z
  .object({
    id: z.string(),
    text: z.string(),
    ts: z.string().optional().default(""),
  })
  .passthrough();
const Profile = z
  .object({
    facts: z.array(Fact),
    raw_long_term: z.array(z.string()),
    episodes: z.array(z.string()),
    version: z.string(),
  })
  .passthrough();
const AddFact = z
  .object({
    text: z.string().min(1),
    expected_version: z.union([z.string(), z.null()]).optional(),
  })
  .passthrough();
const FactsResult = z
  .object({ facts: z.array(Fact), version: z.string() })
  .passthrough();
const UpdateFact = z
  .object({
    text: z.string().min(1),
    expected_version: z.union([z.string(), z.null()]).optional(),
  })
  .passthrough();
const SessionList = z.object({ sessions: z.array(z.string()) }).passthrough();
const Turn = z
  .object({
    role: z.string().default(""),
    content: z.string().default(""),
    ts: z.string().default(""),
  })
  .partial()
  .passthrough();
const SessionDetail = z
  .object({ turns: z.array(Turn), summary: z.string(), pending: z.array(Turn) })
  .passthrough();
const MemoryTriggerResult = z
  .object({ action: z.string(), changed: z.boolean(), detail: z.string() })
  .passthrough();
const RecallPreview = z
  .object({ query: z.string(), sections: z.record(z.string(), z.string()).optional() })
  .passthrough();
const FileHistoryEntry = z
  .object({ sha: z.string(), ts: z.string(), message: z.string() })
  .passthrough();
const FileHistory = z
  .object({ kind: z.string(), entries: z.array(FileHistoryEntry).optional() })
  .passthrough();
const FileVersionOut = z
  .object({ kind: z.string(), sha: z.string(), content: z.string() })
  .passthrough();
const Persona = z.object({ text: z.string() }).passthrough();
const ExpressionOut = z
  .object({
    mime: z.string(),
    filename: z.union([z.string(), z.null()]).optional(),
    version: z.string(),
  })
  .passthrough();
const IdentityOut = z
  .object({
    display_name: z.string().default(""),
    description: z.string().default(""),
    has_avatar: z.boolean().default(false),
    avatar_mime: z.union([z.string(), z.null()]),
    avatar_filename: z.union([z.string(), z.null()]),
    version: z.string().default(""),
    expressions: z.record(z.string(), ExpressionOut),
    moods: z.array(z.string()),
  })
  .partial()
  .passthrough();
const UpdateIdentity = z
  .object({
    display_name: z.string().default(""),
    description: z.string().default(""),
    expected_version: z.union([z.string(), z.null()]),
  })
  .partial()
  .passthrough();
const SetAvatar = z
  .object({
    data_base64: z.string().min(1),
    mime_type: z.string().min(1),
    filename: z.union([z.string(), z.null()]).optional(),
    expected_version: z.union([z.string(), z.null()]).optional(),
  })
  .passthrough();
const MemorySettingsOut = z
  .object({
    memory_dir: z.string(),
    git_enabled: z.boolean(),
    git_author_name: z.string(),
    git_author_email: z.string(),
    active_memory_dir: z.string(),
    restart_required: z.boolean(),
    version: z.string().optional().default(""),
  })
  .passthrough();
const UpdateMemorySettings = z
  .object({
    memory_dir: z.string().default(""),
    git_enabled: z.boolean().default(false),
    git_author_name: z.string().default(""),
    git_author_email: z.string().default(""),
    expected_version: z.union([z.string(), z.null()]),
  })
  .partial()
  .passthrough();
const ProposalOut = z
  .object({
    id: z.string(),
    kind: z.string(),
    target: z.string(),
    current_text: z.string().optional().default(""),
    proposed_text: z.string().optional().default(""),
    rationale: z.string().optional().default(""),
    source: z.string().optional().default(""),
    status: z.string().optional().default("pending"),
    created: z.string().optional().default(""),
    decided: z.string().optional().default(""),
    applied_path: z.string().optional().default(""),
  })
  .passthrough();
const ProposalsOut = z
  .object({
    proposals: z.array(ProposalOut),
    proposable: z.array(z.string()),
    restart_to_apply: z.boolean().default(true),
  })
  .partial()
  .passthrough();
const McpSettingsOut = z
  .object({
    code_servers: z.array(z.object({}).partial().passthrough()),
    operator_servers: z.array(z.object({}).partial().passthrough()),
    version: z.string().default(""),
    restart_required: z.boolean().default(false),
  })
  .partial()
  .passthrough();
const UpdateMcpSettings = z
  .object({
    servers: z.array(z.object({}).partial().passthrough()),
    expected_version: z.union([z.string(), z.null()]),
  })
  .partial()
  .passthrough();
const RawFile = z
  .object({ kind: z.string(), content: z.string(), version: z.string() })
  .passthrough();
const PutRawFile = z
  .object({
    content: z.string(),
    expected_version: z.union([z.string(), z.null()]).optional(),
  })
  .passthrough();

export const schemas = {
  scope,
  DocumentSummaryOut,
  DocumentList,
  ValidationError,
  HTTPValidationError,
  IngestDocument,
  IngestResult,
  TagList,
  SetSubject,
  ChunkOut,
  DocumentDetailOut,
  EditTags,
  RenameDocument,
  SubjectOut,
  SubjectListOut,
  CreateSubject,
  EditSubject,
  UserSummary,
  UserList,
  Fact,
  Profile,
  AddFact,
  FactsResult,
  UpdateFact,
  SessionList,
  Turn,
  SessionDetail,
  MemoryTriggerResult,
  RecallPreview,
  FileHistoryEntry,
  FileHistory,
  FileVersionOut,
  Persona,
  ExpressionOut,
  IdentityOut,
  UpdateIdentity,
  SetAvatar,
  MemorySettingsOut,
  UpdateMemorySettings,
  ProposalOut,
  ProposalsOut,
  McpSettingsOut,
  UpdateMcpSettings,
  RawFile,
  PutRawFile,
};
