import { ChatConsole } from "../../components/ChatConsole";
import { defineFeatureSlice } from "../core";
import * as components from "./components";
import * as hooks from "./hooks";
import * as screens from "./screens";
import * as types from "./types";

export * from "./types";
export * from "./hooks";
export * from "./components";
export * from "./screens";
export * from "./routes";

export const chatSlice = defineFeatureSlice({
  key: "chat",
  title: "Chat",
  description: "Stable MAGI chat building blocks: types, controller helpers, and composition-friendly components.",
  entrypoints: {
    types: "@carneirofc/magi-web/slices/chat/types",
    hooks: "@carneirofc/magi-web/slices/chat/hooks",
    components: "@carneirofc/magi-web/slices/chat/components",
    screens: "@carneirofc/magi-web/slices/chat/screens",
    routes: "@carneirofc/magi-web/slices/chat/routes",
  },
  stable: {
    types,
    hooks,
    components: {
      ChatConsole,
    },
  },
  advanced: {
    components,
    screens,
  },
  internalNotes: [
    "assistant-ui primitives inside ChatConsole remain internal implementation details.",
    "Raw SSE frame handling and blob offload logic stay advanced/internal route helpers rather than root exports.",
  ],
});
