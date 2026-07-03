"""A minimal Tkinter chat window over `magi.client` — the ergonomics, end to end.

Tkinter ships with CPython (no extra dependency), so this runs as-is. It shows
the intended desktop pattern:

  * one `SyncClient` for the app (blocking API over the async client),
  * turns run on a worker thread so the UI never freezes,
  * streamed deltas marshalled back to the UI thread via `root.after`.

Two backends, same code path below — pick one:

    # Talk to a running service (python main_api.py):
    python examples/desktop_chat.py --http http://127.0.0.1:8000

    # Or embed the whole brain in-process (needs a model backend reachable, e.g.
    # a local llama-server on :8888):
    python examples/desktop_chat.py --embedded \
        --model-provider llamacpp --llamacpp-url http://127.0.0.1:8888/v1

Either way you need a model backend somewhere — this demonstrates the client
surface, not a bundled model.
"""

from __future__ import annotations

import argparse
import queue
import threading
import tkinter as tk
from tkinter import scrolledtext

from magi.client import SyncClient, connect, embed


def build_client(args: argparse.Namespace) -> SyncClient:
    """Construct the chosen backend and wrap it for the GUI thread model."""
    if args.embedded:
        overrides: dict[str, object] = {}
        if args.model_provider:
            overrides["model_provider"] = args.model_provider
        if args.llamacpp_url:
            overrides["llamacpp_base_url"] = args.llamacpp_url
        client = embed(user_id=args.user, session_id=args.session, **overrides)
    else:
        client = connect(
            args.http, user_id=args.user, session_id=args.session, auth_token=args.token
        )
    return SyncClient(client)


class ChatWindow:
    """The whole UI: a transcript, an entry box, and a Send button."""

    def __init__(self, root: tk.Tk, client: SyncClient) -> None:
        self._root = root
        self._client = client
        # UI-thread inbox: worker threads push (kind, payload) here; _pump drains it.
        self._events: "queue.Queue[tuple[str, str]]" = queue.Queue()

        root.title(f"magi — {client.user_id}/{client.session_id}")
        self._transcript = scrolledtext.ScrolledText(
            root, wrap=tk.WORD, state=tk.DISABLED, width=80, height=24
        )
        self._transcript.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))

        row = tk.Frame(root)
        row.pack(fill=tk.X, padx=8, pady=(0, 8))
        self._entry = tk.Entry(row)
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._entry.bind("<Return>", lambda _e: self._on_send())
        self._entry.focus_set()
        self._send_btn = tk.Button(row, text="Send", command=self._on_send)
        self._send_btn.pack(side=tk.LEFT, padx=(6, 0))

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._root.after(50, self._pump)

    # --- transcript helpers (UI thread only) --------------------------------
    def _append(self, text: str) -> None:
        self._transcript.configure(state=tk.NORMAL)
        self._transcript.insert(tk.END, text)
        self._transcript.see(tk.END)
        self._transcript.configure(state=tk.DISABLED)

    def _pump(self) -> None:
        """Drain worker events on the UI thread (Tk is single-threaded)."""
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "delta":
                    self._append(payload)
                elif kind == "end":
                    self._append("\n\n")
                    self._send_btn.configure(state=tk.NORMAL)
        except queue.Empty:
            pass
        self._root.after(50, self._pump)

    # --- send flow ----------------------------------------------------------
    def _on_send(self) -> None:
        text = self._entry.get().strip()
        if not text:
            return
        self._entry.delete(0, tk.END)
        self._append(f"you: {text}\n\nmagi: ")
        self._send_btn.configure(state=tk.DISABLED)
        threading.Thread(target=self._run_turn, args=(text,), daemon=True).start()

    def _run_turn(self, text: str) -> None:
        """Runs on a worker thread: stream the reply, push deltas to the UI."""
        from magi.client import Reply

        try:
            for item in self._client.stream(text):
                if isinstance(item, Reply):
                    if not item.text and item.is_error:
                        self._events.put(("delta", "[error]"))
                    if item.media:
                        self._events.put(("delta", f"\n[{len(item.media)} attachment(s)]"))
                else:  # a Delta
                    self._events.put(("delta", item.text))
        except Exception as exc:  # noqa: BLE001 - show it in the transcript
            self._events.put(("delta", f"\n[client error: {exc}]"))
        finally:
            self._events.put(("end", ""))

    def _on_close(self) -> None:
        self._client.close()
        self._root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal magi desktop chat (Tkinter).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--http", metavar="URL", help="talk to a running magi service at URL")
    mode.add_argument(
        "--embedded", action="store_true", help="embed the brain in-process (no server)"
    )
    parser.add_argument("--user", default="local", help="user id (scopes memory)")
    parser.add_argument("--session", default="window-1", help="session id (one conversation)")
    parser.add_argument("--token", default=None, help="API_AUTH_TOKEN, if the service requires it")
    parser.add_argument("--model-provider", default=None, help="[embedded] model provider")
    parser.add_argument("--llamacpp-url", default=None, help="[embedded] llama-server /v1 URL")
    args = parser.parse_args()

    if not args.embedded and not args.http:
        args.http = "http://127.0.0.1:8000"  # sensible default: local main_api.py

    client = build_client(args)
    root = tk.Tk()
    ChatWindow(root, client)
    root.mainloop()


if __name__ == "__main__":
    main()
