// Recorded speech → server STT → composer text. The server-backed sibling of
// chat-dictation.ts: where that one wraps the browser's Web Speech API (absent
// in the QtWebEngine desktop shell), this records the mic with MediaRecorder
// and transcribes through the BFF relay (/api/chat/stt → chat-api /v1/stt →
// the whisper-class sidecar), so the mic works anywhere getUserMedia does.
//
// Same assistant-ui `DictationAdapter` seam, so the composer's mic button is
// unchanged. Timing contract (see base-composer-runtime-core): the transcript
// must be emitted via `onSpeech({isFinal: true})` BEFORE `stop()` resolves —
// the runtime unsubscribes right after stop() settles — so the STT round-trip
// happens inside stop() while the composer shows its "dictating" state.

import type { DictationAdapter } from "@assistant-ui/react";

/** MediaRecorder mimes we try, most-compatible first; the STT sidecar
 * (whisper-class) decodes all of them. */
const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
];

function pickMime(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  return MIME_CANDIDATES.find((m) => MediaRecorder.isTypeSupported(m));
}

function extensionFor(mime: string): string {
  if (mime.includes("mp4")) return "m4a";
  if (mime.includes("ogg")) return "ogg";
  return "webm";
}

/** Whether this browser can record audio at all (getUserMedia + MediaRecorder).
 * True in every modern browser AND the QtWebEngine desktop shell — the shell
 * auto-grants the mic to its own loopback frontend. */
export function recordingSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined"
  );
}

const MIC_HINT =
  "Couldn't start recording — allow microphone access (or check that a mic is connected) and try again.";
const STT_HINT =
  "Couldn't transcribe the recording — is the speech-to-text service up? The text stays as you typed it.";

/** A `DictationAdapter` that records and transcribes server-side. `onError`
 * surfaces failures as the composer's dismissible hint; `onListeningChange`
 * mirrors mic activity into the voice signal (the stage's listening glow). */
export function createRecordingDictationAdapter(
  onError: (message: string) => void,
  onListeningChange?: (listening: boolean) => void,
): DictationAdapter {
  return {
    listen(): DictationAdapter.Session {
      const speechListeners = new Set<(result: DictationAdapter.Result) => void>();
      const startListeners = new Set<() => void>();
      const endListeners = new Set<(result: DictationAdapter.Result) => void>();

      let recorder: MediaRecorder | null = null;
      let stream: MediaStream | null = null;
      const chunks: Blob[] = [];
      const mime = pickMime();

      const session = {
        status: { type: "starting" } as DictationAdapter.Status,
        async stop() {
          onListeningChange?.(false);
          if (!recorder || session.status.type === "ended") {
            end("stopped");
            return;
          }

          // Flush the recorder into one blob, then release the mic.
          const active = recorder;
          const blob = await new Promise<Blob>((resolve) => {
            active.onstop = () =>
              resolve(new Blob(chunks, { type: active.mimeType || mime || "audio/webm" }));
            try {
              active.stop();
            } catch {
              resolve(new Blob(chunks, { type: mime ?? "audio/webm" }));
            }
          });
          releaseStream();

          if (blob.size === 0) {
            end("error");
            onError(MIC_HINT);
            return;
          }

          // Transcribe while the composer still shows the dictation state; the
          // final transcript must land via onSpeech BEFORE this promise
          // resolves (the runtime unsubscribes right after).
          try {
            const form = new FormData();
            form.append("file", blob, `dictation.${extensionFor(blob.type)}`);
            const res = await fetch("/api/chat/stt", { method: "POST", body: form });
            if (!res.ok) throw new Error(`stt ${res.status}`);
            const body = (await res.json()) as { text?: string };
            const transcript = (body.text ?? "").trim();
            if (transcript) {
              const result: DictationAdapter.Result = { transcript, isFinal: true };
              for (const listener of [...speechListeners]) listener(result);
              for (const listener of [...endListeners]) listener(result);
            }
            end("stopped");
          } catch {
            end("error");
            onError(STT_HINT);
          }
        },
        cancel() {
          try {
            recorder?.stop();
          } catch {
            /* already stopped */
          }
          releaseStream();
          onListeningChange?.(false);
          end("cancelled");
        },
        onSpeechStart(callback: () => void) {
          startListeners.add(callback);
          return () => {
            startListeners.delete(callback);
          };
        },
        onSpeechEnd(callback: (result: DictationAdapter.Result) => void) {
          endListeners.add(callback);
          return () => {
            endListeners.delete(callback);
          };
        },
        onSpeech(callback: (result: DictationAdapter.Result) => void) {
          speechListeners.add(callback);
          return () => {
            speechListeners.delete(callback);
          };
        },
      };

      function end(reason: "stopped" | "cancelled" | "error") {
        if (session.status.type !== "ended") session.status = { type: "ended", reason };
      }

      function releaseStream() {
        for (const track of stream?.getTracks() ?? []) track.stop();
        stream = null;
        recorder = null;
      }

      // Acquire the mic and start recording. listen() itself is synchronous —
      // a permission denial ends the session as "error", which the status
      // poller in the runtime (and our error hint) picks up.
      navigator.mediaDevices
        .getUserMedia({ audio: true })
        .then((acquired) => {
          if (session.status.type === "ended") {
            for (const track of acquired.getTracks()) track.stop();
            return;
          }
          stream = acquired;
          recorder = new MediaRecorder(acquired, mime ? { mimeType: mime } : undefined);
          recorder.ondataavailable = (event) => {
            if (event.data.size > 0) chunks.push(event.data);
          };
          recorder.start(250); // small timeslices so stop() flushes fast
          session.status = { type: "running" };
          onListeningChange?.(true);
          for (const listener of [...startListeners]) listener();
        })
        .catch(() => {
          end("error");
          onError(MIC_HINT);
        });

      return session;
    },
  };
}
