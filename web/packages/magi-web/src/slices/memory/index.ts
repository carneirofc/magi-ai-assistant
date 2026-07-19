// The memory slice: the operator's window onto durable memory — the user
// roster, a user's curated facts / raw long-term / episodes / sessions, and one
// session's machine-managed state (window, rolling summary, pending buffer),
// plus the operator-triggered passes (summarize / curate / flush / consolidate)
// and recall-preview retrieval lens.
//
// The real contract is types + hooks + components; the default screens are a
// convenience composition on top.

import { FactEditor } from "../../components/FactEditor";
import { MemoryMaintenance } from "../../components/MemoryMaintenance";
import { MemoryTabs } from "../../components/MemoryTabs";
import { RawFileEditor } from "../../components/RawFileEditor";
import { SessionFile } from "../../components/SessionFile";
import { SessionMemoryActions } from "../../components/SessionMemoryActions";
import { UserGrid } from "../../components/UserGrid";
import { defineFeatureSlice } from "../core";
import * as components from "./components";
import * as hooks from "./hooks";
import * as screens from "./screens";
import * as types from "./types";

export * from "./types";
export * from "./hooks";
export * from "./components";
export * from "./screens";

export const memorySlice = defineFeatureSlice({
  key: "memory",
  title: "Memory",
  description:
    "Stable MAGI memory building blocks for browsing users, editing curated facts / raw long-term / episodes, and inspecting a session's machine-managed state.",
  entrypoints: {
    types: "@carneirofc/magi-web/slices/memory/types",
    hooks: "@carneirofc/magi-web/slices/memory/hooks",
    components: "@carneirofc/magi-web/slices/memory/components",
    screens: "@carneirofc/magi-web/slices/memory/screens",
  },
  stable: {
    types,
    hooks,
    components: {
      UserGrid,
      MemoryTabs,
      FactEditor,
      RawFileEditor,
      MemoryMaintenance,
      SessionFile,
      SessionMemoryActions,
    },
  },
  advanced: {
    components,
    screens,
  },
  internalNotes: [
    "Memory routes stay under admin/* because they are BFF proxies, not slice-owned Next route files.",
    "MemorySettingsEditor is exported for reuse but is bound to the settings page's AdminMemorySettings shape.",
    "MemoryPanel (the ambient companion panel) is owned by the companion slice, not memory.",
  ],
});
