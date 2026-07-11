import { AddKnowledge } from "../../components/AddKnowledge";
import { DocumentActions } from "../../components/DocumentActions";
import { DocumentMeta } from "../../components/DocumentMeta";
import { KnowledgeList } from "../../components/KnowledgeList";
import { defineFeatureSlice } from "../core";
import * as components from "./components";
import * as hooks from "./hooks";
import * as screens from "./screens";
import * as types from "./types";

export * from "./types";
export * from "./hooks";
export * from "./components";
export * from "./screens";

export const knowledgeSlice = defineFeatureSlice({
  key: "knowledge",
  title: "Knowledge",
  description: "Stable MAGI knowledge building blocks for browsing, ingesting, and editing corpus documents.",
  entrypoints: {
    types: "@carneirofc/magi-web/slices/knowledge/types",
    hooks: "@carneirofc/magi-web/slices/knowledge/hooks",
    components: "@carneirofc/magi-web/slices/knowledge/components",
    screens: "@carneirofc/magi-web/slices/knowledge/screens",
  },
  stable: {
    types,
    hooks,
    components: {
      KnowledgeList,
      AddKnowledge,
      DocumentActions,
      DocumentMeta,
    },
  },
  advanced: {
    components,
    screens,
  },
  internalNotes: [
    "Knowledge routes still live under admin/* because they are BFF proxies, not slice-owned Next route files.",
  ],
});
