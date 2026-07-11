// The companion slice: the persona visually present beside the chat — a mood-
// reactive portrait stage (PersonaStage), the responsive side-stage ↔ header-
// bust arrangement (CompanionSurface), and the shared mood signal they read
// (see the chat slice's useMood/createChatModelAdapter for the producing side).

import { CompanionSurface } from "../../components/CompanionSurface";
import { PersonaStage } from "../../components/PersonaStage";
import { defineFeatureSlice } from "../core";
import * as components from "./components";

export * from "./components";

export const companionSlice = defineFeatureSlice({
  key: "companion",
  title: "Companion",
  description:
    "Companion chat surface: the persona's mood-reactive portrait stage and the responsive layout that keeps it beside the transcript.",
  entrypoints: {
    types: "@carneirofc/magi-web/slices/chat/types",
    hooks: "@carneirofc/magi-web/slices/chat/hooks",
    components: "@carneirofc/magi-web/slices/companion/components",
  },
  stable: {
    types: {},
    hooks: {},
    components: {
      CompanionSurface,
      PersonaStage,
    },
  },
  advanced: {
    components,
  },
  internalNotes: [
    "The mood signal itself (MoodProvider/useMood + the adapter frames) lives in the chat slice; this slice only consumes it.",
  ],
});
