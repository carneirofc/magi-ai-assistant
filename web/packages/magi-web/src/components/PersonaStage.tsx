"use client";

// PersonaStage — the assistant's face on screen: a portrait that swaps
// expression with the streamed mood (the engine's pre-reply pass, see
// chat-mood.tsx) and carries the turn's lifecycle as visual treatment.
//
// Presentational by design: the caller supplies the expression map (mood →
// image URL — identity-API URLs and bundled static assets both work) and this
// component only renders. It reads the ambient MoodProvider/MoodScope, so put
// it under the same provider as the chat surface (the companion layout does).
//
// Behavior:
//   - crossfade between expressions on mood change (both images stay mounted
//     during the fade, so there's never a flash of empty stage);
//   - idle micro-motion: a slow breathe (compositor-only transform, no layout);
//   - lifecycle treatments while a turn is in flight: dim-pulse when thinking,
//     full presence when streaming, a working dot when a tool runs, desaturate
//     on error;
//   - voice treatments when an ambient VoiceProvider is mounted (optional —
//     see chat-voice.tsx): a talk-pulse while a TTS clip plays, a listening
//     glow while the mic records; without a provider these simply never show;
//   - a mood with no image falls back to `neutral`; no usable art at all
//     renders a quiet monogram placeholder instead of a broken image.

import { useEffect, useMemo, useRef, useState } from "react";

import { useMood, type ChatLifecycle } from "../lib/chat-mood";
import { useVoiceOptional } from "../lib/chat-voice";

export type PersonaStageProps = {
  /** mood name → portrait URL. `neutral` doubles as the fallback face. */
  expressions: Record<string, string>;
  /** The persona's display name — the monogram placeholder + alt text. */
  name?: string | null;
  /** Which map entry is the resting/fallback face (default "neutral"). */
  fallbackMood?: string;
  className?: string;
};

/** The URL to show for a mood: exact entry, else the fallback face, else null. */
export function resolveExpression(
  expressions: Record<string, string>,
  mood: string | null,
  fallbackMood: string,
): string | null {
  if (mood && expressions[mood]) return expressions[mood];
  return expressions[fallbackMood] ?? null;
}

const LIFECYCLE_LABEL: Record<ChatLifecycle, string | null> = {
  idle: null,
  thinking: "thinking",
  streaming: "replying",
  tool: "working",
  error: "connection trouble",
};

export function PersonaStage({
  expressions,
  name = null,
  fallbackMood = "neutral",
  className = "",
}: PersonaStageProps) {
  const { mood, lifecycle } = useMood();
  // Voice is an optional enhancement: no ambient VoiceProvider (an app without
  // TTS/STT) simply never shows the speaking/listening treatments.
  const voice = useVoiceOptional();
  const speaking = voice?.speaking ?? false;
  const listening = voice?.listening ?? false;
  const src = resolveExpression(expressions, mood, fallbackMood);

  // Crossfade: the outgoing portrait stays mounted underneath while the new one
  // fades in, then gets dropped. Keyed by URL so a mood switch that resolves to
  // the same art (e.g. two moods sharing the fallback) doesn't re-fade.
  const [layers, setLayers] = useState<string[]>(src ? [src] : []);
  const current = layers[layers.length - 1] ?? null;
  useEffect(() => {
    if (!src || src === current) return;
    setLayers((prev) => [...prev.slice(-1), src]);
  }, [src, current]);
  const fadeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (layers.length < 2) return;
    if (fadeTimer.current) clearTimeout(fadeTimer.current);
    fadeTimer.current = setTimeout(() => setLayers((prev) => prev.slice(-1)), 450);
    return () => {
      if (fadeTimer.current) clearTimeout(fadeTimer.current);
    };
  }, [layers]);

  // Voice states outrank lifecycle in the caption: while she's audibly
  // speaking (or the mic is hot) that IS the status.
  const label = listening ? "listening" : speaking ? "speaking" : LIFECYCLE_LABEL[lifecycle];
  const treatment = useMemo(() => {
    if (speaking) return "magi-stage--speaking";
    if (listening) return "magi-stage--listening";
    switch (lifecycle) {
      case "thinking":
        return "magi-stage--thinking";
      case "tool":
        return "magi-stage--thinking";
      case "error":
        return "magi-stage--error";
      default:
        return "";
    }
  }, [lifecycle, speaking, listening]);

  return (
    <figure
      className={`relative flex flex-col items-center gap-2 ${className}`}
      aria-label={name ? `${name} — ${mood ?? "no mood yet"}` : undefined}
    >
      {/* Component-scoped keyframes: the consuming app's Tailwind build knows
          nothing about these, so they ship inline. */}
      <style>{`
        @keyframes magi-stage-breathe {
          0%, 100% { transform: translateY(0) scale(1); }
          50% { transform: translateY(-1.5px) scale(1.008); }
        }
        @keyframes magi-stage-pulse {
          0%, 100% { filter: brightness(0.92); }
          50% { filter: brightness(1); }
        }
        @keyframes magi-stage-talk {
          0%, 100% { transform: scale(1); filter: brightness(1); }
          50% { transform: scale(1.012); filter: brightness(1.06); }
        }
        @keyframes magi-stage-listen {
          0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--ui-ink-accent) 45%, transparent); }
          50% { box-shadow: 0 0 0 6px color-mix(in srgb, var(--ui-ink-accent) 12%, transparent); }
        }
        .magi-stage-motion { animation: magi-stage-breathe 6s ease-in-out infinite; }
        .magi-stage--thinking { animation: magi-stage-pulse 1.8s ease-in-out infinite; }
        .magi-stage--speaking { animation: magi-stage-talk 0.9s ease-in-out infinite; }
        .magi-stage--listening { animation: magi-stage-listen 1.6s ease-in-out infinite; }
        .magi-stage--error { filter: saturate(0.35) brightness(0.85); }
        @media (prefers-reduced-motion: reduce) {
          .magi-stage-motion, .magi-stage--thinking, .magi-stage--speaking,
          .magi-stage--listening { animation: none; }
        }
      `}</style>

      <div
        className={`magi-stage-motion relative aspect-[3/4] w-full overflow-hidden rounded-xl border border-ui bg-[color:var(--ui-bg-soft)] transition-[filter] duration-300 ${treatment}`}
      >
        {current ? (
          layers.map((url, i) => (
            <img
              key={url}
              src={url}
              alt={i === layers.length - 1 ? (name ?? "assistant portrait") : ""}
              aria-hidden={i !== layers.length - 1}
              draggable={false}
              className={`absolute inset-0 h-full w-full select-none object-cover ${
                i === layers.length - 1 ? "magi-stage-fade-in" : ""
              }`}
              style={
                i === layers.length - 1 && layers.length > 1
                  ? { animation: "magi-stage-fade 0.45s ease both" }
                  : undefined
              }
            />
          ))
        ) : (
          <div className="flex h-full w-full items-center justify-center text-4xl font-semibold text-[color:var(--ui-ink-subtle)]">
            {(name ?? "?").slice(0, 1).toUpperCase()}
          </div>
        )}
        <style>{`
          @keyframes magi-stage-fade { from { opacity: 0; } to { opacity: 1; } }
        `}</style>
      </div>

      <figcaption className="flex items-center gap-2 text-ui-2xs text-[color:var(--ui-ink-subtle)]">
        {mood ? <span className="font-mono text-[color:var(--ui-ink-accent)]">{mood}</span> : null}
        {label ? (
          <span className="flex items-center gap-1">
            {lifecycle !== "error" ? (
              <span
                aria-hidden
                className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-current"
              />
            ) : (
              <span aria-hidden>⚠</span>
            )}
            {label}
          </span>
        ) : null}
      </figcaption>
    </figure>
  );
}
