"use client";

// Renders a ```mermaid fenced block as an SVG diagram instead of a code chip.
// Wired into the chat markdown via MarkdownTextPrimitive's `componentsByLanguage`
// (see ChatConsole.tsx), so anything the model draws in Mermaid — flowcharts,
// sequence/state/ER diagrams — shows up as a real picture in the transcript.
//
// beautiful-mermaid renders synchronously to an SVG string (no async, no DOM
// dependency, works with useMemo). We drive its colors off the same @carneirofc/ui
// tokens as the rest of the console by handing it `var(--ui-*)` references, which
// it writes onto the SVG as CSS custom properties — so the diagram re-themes with
// the dashboard (light/dark) without a re-render. When the source doesn't parse we
// fall back to the plain fenced-code rendering rather than throwing.

import { useMemo, useState } from "react";
import { renderMermaidSVG } from "beautiful-mermaid";
import type { SyntaxHighlighterProps } from "@assistant-ui/react-markdown";

// Map the diagram's palette onto our theme tokens. beautiful-mermaid emits these
// as `--bg`/`--fg`/… on the SVG, defaulted to whatever we pass here — so a plain
// `var(--ui-*)` reference cascades and tracks the active theme.
const THEME_OPTIONS = {
  transparent: true,
  fg: "var(--ui-ink)",
  line: "var(--ui-border-strong, var(--ui-ink-subtle))",
  accent: "var(--ui-ink-accent)",
  muted: "var(--ui-ink-subtle)",
  surface: "var(--ui-bg-soft)",
  border: "var(--ui-border, var(--ui-ink-subtle))",
} as const;

export function MermaidDiagram({ code, components }: SyntaxHighlighterProps) {
  const [showSource, setShowSource] = useState(false);

  // Render once per unique source. On a parse/render error, `svg` is null and we
  // drop back to the raw fenced block so a malformed diagram is still readable.
  const svg = useMemo(() => {
    try {
      return renderMermaidSVG(code, THEME_OPTIONS);
    } catch {
      return null;
    }
  }, [code]);

  if (svg === null) {
    return (
      <components.Pre>
        <components.Code>{code}</components.Code>
      </components.Pre>
    );
  }

  return (
    <div className="my-2 overflow-hidden rounded-md border border-ui bg-[color:var(--ui-bg-soft)]">
      <div className="flex items-center justify-between border-b border-ui px-2 py-1">
        <span className="text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
          Mermaid
        </span>
        <button
          type="button"
          onClick={() => setShowSource((v) => !v)}
          className="text-ui-2xs text-[color:var(--ui-ink-subtle)] hover:text-[color:var(--ui-ink-accent)]"
          title={showSource ? "Show the rendered diagram" : "Show the diagram source"}
        >
          {showSource ? "Diagram" : "Source"}
        </button>
      </div>
      {showSource ? (
        <components.Pre>
          <components.Code>{code}</components.Code>
        </components.Pre>
      ) : (
        <div
          className="overflow-x-auto p-3 [&>svg]:mx-auto [&>svg]:h-auto [&>svg]:max-w-full"
          // beautiful-mermaid output is a self-contained SVG string we trust.
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      )}
    </div>
  );
}
