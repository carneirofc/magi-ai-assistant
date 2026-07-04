"use client";

// Syntax highlighting for the chat's fenced code blocks. Wired into the chat
// markdown via MarkdownTextPrimitive's `SyntaxHighlighter` / `CodeHeader` slots
// (see ChatConsole.tsx), so every ```lang fence the model emits renders as a
// highlighted card: a header strip (language label + copy button) over a Shiki-
// rendered body.
//
// Shiki runs entirely client-side (react-shiki, full bundle) — no network, so it
// works in the offline desktop shell. We hand it a light+dark theme pair and let
// the browser pick per token via CSS `light-dark()`; globals.css maps the app's
// `data-theme` to `color-scheme` so the code re-themes with the dashboard toggle.
// A `delay` throttles re-highlighting while a reply streams in token by token.
//
// Note: assistant-ui only routes a fence through `SyntaxHighlighter` when it has a
// language; bare ``` fences fall back to the themed <pre>/<code> in ChatConsole.
// `CodeHeader` is still invoked for those, so it renders nothing without a language.

import { useCallback, useState } from "react";
import ShikiHighlighter from "react-shiki";
import type { CodeHeaderProps, SyntaxHighlighterProps } from "@assistant-ui/react-markdown";

const THEMES = { light: "github-light", dark: "github-dark" } as const;

// Copy-to-clipboard control for the code header. Flips to a "Copied" state for a
// beat so the click is acknowledged, then falls back silently if the clipboard is
// unavailable (e.g. an insecure context).
function CopyButton({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(() => {
    void navigator.clipboard
      ?.writeText(code)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => setCopied(false));
  }, [code]);

  return (
    <button
      type="button"
      onClick={onCopy}
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-ui-2xs text-[color:var(--ui-ink-subtle)] transition-colors hover:bg-[color:var(--ui-bg)] hover:text-[color:var(--ui-ink)]"
      title="Copy code"
      aria-label={copied ? "Copied" : "Copy code"}
    >
      {copied ? (
        <>
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M20 6 9 17l-5-5" />
          </svg>
          Copied
        </>
      ) : (
        <>
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <rect x="9" y="9" width="11" height="11" rx="2" />
            <path d="M5 15V5a2 2 0 0 1 2-2h10" />
          </svg>
          Copy
        </>
      )}
    </button>
  );
}

// The strip above a highlighted block: language name on the left, copy on the
// right. Renders nothing for language-less fences (matches assistant-ui's default).
export function CodeHeader({ language, code }: CodeHeaderProps) {
  if (!language) return null;
  return (
    <div className="flex items-center justify-between gap-2 rounded-t-md border border-b-0 border-ui bg-[color:var(--ui-bg-soft)] px-3 py-1">
      <span className="font-mono text-ui-2xs uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
        {language}
      </span>
      <CopyButton code={code} />
    </div>
  );
}

// The highlighted body. addDefaultStyles is off so we own the chrome: the outer
// div carries the border/rounding that meets the header above, and overflow-hidden
// clips Shiki's own <pre> corners. `light-dark()` resolves each token color from
// the ancestor color-scheme (see globals.css → [data-theme]).
export function CodeSyntaxHighlighter({ language, code }: SyntaxHighlighterProps) {
  return (
    <ShikiHighlighter
      as="div"
      language={language || "text"}
      theme={THEMES}
      defaultColor="light-dark()"
      delay={120}
      showLanguage={false}
      addDefaultStyles={false}
      className="overflow-hidden rounded-b-md border border-t-0 border-ui text-ui-xs [&_pre]:m-0 [&_pre]:overflow-x-auto [&_pre]:p-3 [&_pre]:leading-relaxed"
    >
      {code}
    </ShikiHighlighter>
  );
}
