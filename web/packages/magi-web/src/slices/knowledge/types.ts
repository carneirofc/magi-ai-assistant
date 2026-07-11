import type { listKnowledgeDocuments, getKnowledgeDocument, listSubjects, listTags } from "../../lib/admin-api";

export type KnowledgeDocumentListItem = Awaited<ReturnType<typeof listKnowledgeDocuments>>["documents"][number];
export type KnowledgeDocument = Awaited<ReturnType<typeof getKnowledgeDocument>>;
export type KnowledgeSubject = Awaited<ReturnType<typeof listSubjects>>["subjects"][number];
export type KnowledgeTag = Awaited<ReturnType<typeof listTags>>["tags"][number];
