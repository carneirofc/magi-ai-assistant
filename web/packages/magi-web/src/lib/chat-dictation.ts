// Voice → text for the composer. Wraps assistant-ui's WebSpeechDictationAdapter
// (browser Web Speech API, no server round-trip) so the one thing the base
// adapter swallows — an errored session — becomes visible to the operator.
//
// The base adapter only `console.error`s when recognition fails (denied mic
// permission, no microphone, no speech), so clicking the mic looks like nothing
// happened. We can't see the underlying error string through the Session type, so
// we watch the session status: a run that ends with reason "error" (rather than a
// clean "stopped"/"cancelled") means the mic never got going. That fires onError,
// which the composer renders as a dismissible hint.

import { WebSpeechDictationAdapter, type DictationAdapter } from "@assistant-ui/react";

export function dictationSupported(): boolean {
  return WebSpeechDictationAdapter.isSupported();
}

export function createDictationAdapter(
  onError: (message: string) => void,
): DictationAdapter {
  const inner = new WebSpeechDictationAdapter();

  return {
    listen() {
      let session: DictationAdapter.Session;
      try {
        session = inner.listen();
      } catch (error) {
        onError(
          "Couldn't start dictation — allow microphone access for this site (or check that a mic is connected) and try again.",
        );
        throw error;
      }

      // Poll the session status until it settles. A permission denial ends the
      // session as "error" without ever reaching "running".
      let settled = false;
      const poll = () => {
        if (settled) return;
        if (session.status.type === "ended") {
          settled = true;
          if (session.status.reason === "error") {
            onError(
              "Couldn't start dictation — allow microphone access for this site (or check that a mic is connected) and try again.",
            );
          }
          return;
        }
        window.setTimeout(poll, 120);
      };
      poll();

      return session;
    },
  };
}
