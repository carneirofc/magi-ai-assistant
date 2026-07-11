"use client";

// The chat's voice-out signal — playback state + the auto-speak preference —
// lifted out of the console exactly like chat-mood.tsx, so anything on the page
// (the persona stage, a toggle near the portrait) can see and drive it.
//
// One controller owns the <audio> pipeline: speak(text, mood) fetches the BFF
// TTS relay (/api/chat/tts → chat-api /v1/tts → the TTS sidecar), plays the
// clip, and keeps `speaking` true until it ends. The reply's mood rides along
// so the sidecar's per-mood style (engine `tts_mood_styles`) shapes delivery —
// the voice tracks the face. Failures are silent by design: no sidecar, an
// autoplay block, a mid-clip stop — the text is already on screen, so voice
// only ever adds.
//
// Composition contract (same as MoodProvider/MoodScope): wrap the surface in
// <VoiceProvider> to share the signal; the console's own <VoiceScope> joins an
// ambient provider or scopes local state so it works standalone. PersonaStage
// reads `useVoiceOptional` — no provider, no voice treatments, no crash.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { SpeechSynthesisAdapter } from "@assistant-ui/react";

/** localStorage key for the auto-speak preference (shared across sessions). */
const AUTO_SPEAK_KEY = "magi.chat.voice.v1";

export type VoiceState = {
  /** Speak every finished reply aloud (persisted preference). */
  autoSpeak: boolean;
  /** A clip is currently playing (drives the stage's speaking treatment). */
  speaking: boolean;
  /** The mic is recording (drives the stage's listening treatment). */
  listening: boolean;
};

export type VoiceContextValue = VoiceState & {
  setAutoSpeak: (on: boolean) => void;
  setListening: (on: boolean) => void;
  /** Speak `text` through the TTS relay; stops whatever was playing first.
   * `onEnd` fires exactly once with how the utterance finished. */
  speak: (
    text: string,
    mood?: string | null,
    onEnd?: (reason: "finished" | "cancelled" | "error") => void,
  ) => void;
  stopSpeaking: () => void;
};

const VoiceContext = createContext<VoiceContextValue | null>(null);

function useVoiceStateInternal(): VoiceContextValue {
  const [autoSpeak, setAutoSpeakState] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [listening, setListening] = useState(false);

  // The playback pipeline. `requestId` invalidates stale fetches/clips when a
  // newer speak()/stop() supersedes them; `onEndRef` is the current utterance's
  // completion callback (fired exactly once).
  const requestId = useRef(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const onEndRef = useRef<((reason: "finished" | "cancelled" | "error") => void) | null>(null);

  useEffect(() => {
    setAutoSpeakState(localStorage.getItem(AUTO_SPEAK_KEY) === "1");
  }, []);

  const teardown = useCallback((reason: "finished" | "cancelled" | "error") => {
    requestId.current++;
    const audio = audioRef.current;
    audioRef.current = null;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    const onEnd = onEndRef.current;
    onEndRef.current = null;
    setSpeaking(false);
    onEnd?.(reason);
  }, []);

  const stopSpeaking = useCallback(() => teardown("cancelled"), [teardown]);

  // Stop playback when the surface unmounts (leaving the page mid-sentence).
  useEffect(() => () => teardown("cancelled"), [teardown]);

  const speak = useCallback<VoiceContextValue["speak"]>(
    (text, mood = null, onEnd) => {
      const clean = text.trim();
      if (!clean) {
        onEnd?.("finished");
        return;
      }
      teardown("cancelled");
      const id = ++requestId.current;
      onEndRef.current = onEnd ?? null;
      setSpeaking(true);

      fetch("/api/chat/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: clean, mood }),
      })
        .then((res) => (res.ok ? res.blob() : Promise.reject(new Error(`tts ${res.status}`))))
        .then((blob) => {
          if (requestId.current !== id) return; // superseded while fetching
          const url = URL.createObjectURL(blob);
          urlRef.current = url;
          const audio = new Audio(url);
          audioRef.current = audio;
          audio.onended = () => {
            if (requestId.current === id) teardown("finished");
          };
          audio.onerror = () => {
            if (requestId.current === id) teardown("error");
          };
          // play() rejects under an autoplay block (no user gesture yet — e.g.
          // a greeting on a fresh page load). Silent degrade: text is on screen.
          return audio.play().catch(() => {
            if (requestId.current === id) teardown("error");
          });
        })
        .catch(() => {
          if (requestId.current === id) teardown("error");
        });
    },
    [teardown],
  );

  const setAutoSpeak = useCallback((on: boolean) => {
    setAutoSpeakState(on);
    localStorage.setItem(AUTO_SPEAK_KEY, on ? "1" : "0");
  }, []);

  return useMemo(
    () => ({ autoSpeak, speaking, listening, setAutoSpeak, setListening, speak, stopSpeaking }),
    [autoSpeak, speaking, listening, setAutoSpeak, speak, stopSpeaking],
  );
}

/** Shares the voice signal with everything under it (stage, toggle, console).
 * Place it ABOVE both the chat console and whatever should react to it. */
export function VoiceProvider({ children }: { children: ReactNode }) {
  const value = useVoiceStateInternal();
  return <VoiceContext.Provider value={value}>{children}</VoiceContext.Provider>;
}

/** The shared voice signal. Requires a `VoiceProvider`/`VoiceScope` above. */
export function useVoice(): VoiceContextValue {
  const value = useContext(VoiceContext);
  if (value === null) {
    throw new Error("useVoice needs a <VoiceProvider> above it");
  }
  return value;
}

/** The ambient voice signal, or null when no provider is mounted — for
 * components that treat voice as an optional enhancement (PersonaStage). */
export function useVoiceOptional(): VoiceContextValue | null {
  return useContext(VoiceContext);
}

/** Join the ambient `VoiceProvider` when one is mounted, else scope the state
 * here — the console wraps itself in this, mirroring `MoodScope`. */
export function VoiceScope({ children }: { children: ReactNode }) {
  const ambient = useContext(VoiceContext);
  const local = useVoiceStateInternal();
  return (
    <VoiceContext.Provider value={ambient ?? local}>{children}</VoiceContext.Provider>
  );
}

/** assistant-ui `SpeechSynthesisAdapter` over the shared controller, so the
 * message action bar's Speak/StopSpeaking primitives drive the same pipeline
 * (and the same `speaking` stage treatment) as auto-speak. `getMood` is read at
 * click time — a replayed message speaks in the face's current mood. Takes just
 * the two controller functions (stable across renders), so the adapter can be
 * memoized once. */
export function createSpeechAdapter(
  voice: Pick<VoiceContextValue, "speak" | "stopSpeaking">,
  getMood: () => string | null,
): SpeechSynthesisAdapter {
  return {
    speak(text: string): SpeechSynthesisAdapter.Utterance {
      const listeners = new Set<() => void>();
      const utterance = {
        status: { type: "running" } as SpeechSynthesisAdapter.Status,
        cancel: () => voice.stopSpeaking(),
        subscribe(callback: () => void) {
          listeners.add(callback);
          return () => {
            listeners.delete(callback);
          };
        },
      };
      voice.speak(text, getMood(), (reason) => {
        utterance.status = { type: "ended", reason };
        for (const listener of [...listeners]) listener();
      });
      return utterance;
    },
  };
}
