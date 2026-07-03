"""A blocking wrapper for GUI toolkits.

Desktop toolkits (Tkinter, PyQt/PySide, wxPython) own the main thread with their
own event loop, so `asyncio.run()` on the main thread is awkward and blocking the
UI thread on a turn freezes the window. `SyncClient` sidesteps both: it runs one
asyncio loop on a private daemon thread and marshals every call onto it, exposing
plain blocking methods over any async `MagiClient`.

Typical GUI use: build it once, then call `send`/`stream` from a **worker
thread** (so the UI thread stays responsive) and marshal results back to the UI
thread via the toolkit's own mechanism (`root.after`, Qt signals, …). Calling
from the UI thread also works — it just blocks until the turn completes.

    client = SyncClient(embed(user_id="local"))   # or connect(...)
    reply = client.send("hello")
    for chunk in client.stream("tell me more"):
        ...
    client.close()

`stream` returns an ordinary generator that bridges the async generator across
the thread boundary, so the caller iterates it like any other.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Coroutine, Iterator, Sequence
from typing import Optional, TypeVar, Union

from magi.client.base import MagiClient
from magi.client.types import Delta, InboundImage, Reply

_T = TypeVar("_T")

# One item handed from the loop thread to the caller: a streamed value, or an
# exception, or (None, None) as the end-of-stream sentinel. A streamed value is
# always a truthy Delta/Reply, so `item is None` unambiguously marks the end.
_BridgeItem = tuple[Union[Delta, Reply, None], Optional[BaseException]]


class SyncClient:
    """Blocking facade over an async `MagiClient`, backed by a private loop thread.

    Owns the wrapped client's lifecycle: `aopen` runs at construction, `aclose`
    at `close()`. Use as a context manager to guarantee cleanup.
    """

    def __init__(self, client: MagiClient) -> None:
        self._client = client
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="magi-client-loop", daemon=True
        )
        self._thread.start()
        self._run(client.aopen())

    @property
    def user_id(self) -> str:
        return self._client.user_id

    @property
    def session_id(self) -> str:
        return self._client.session_id

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, coro: Coroutine[object, object, _T]) -> _T:
        """Submit a coroutine to the loop thread and block for its result."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def send(self, text: str, *, images: Sequence[InboundImage] = ()) -> Reply:
        return self._run(self._client.send(text, images=images))

    def stream(
        self, text: str, *, images: Sequence[InboundImage] = ()
    ) -> Iterator[Union[Delta, Reply]]:
        """Drive the async stream on the loop thread, yielding items synchronously.

        A bounded queue hands items from the loop thread to the caller; an
        exception raised inside the stream is re-raised here, in the caller.
        """
        bridge: "queue.Queue[_BridgeItem]" = queue.Queue(maxsize=64)

        async def drain() -> None:
            try:
                async for item in self._client.stream(text, images=images):
                    bridge.put((item, None))
            except BaseException as exc:  # noqa: BLE001 - surfaced to the caller below
                bridge.put((None, exc))
            finally:
                bridge.put((None, None))  # end-of-stream sentinel

        future = asyncio.run_coroutine_threadsafe(drain(), self._loop)
        try:
            while True:
                item, exc = bridge.get()
                if exc is not None:
                    raise exc
                if item is None:  # end-of-stream (a real item is always truthy)
                    break
                yield item
        finally:
            future.cancel()  # no-op if already finished; stops a half-consumed stream

    def flush(self) -> int:
        return self._run(self._client.flush())

    def context_stats(self) -> dict[str, object]:
        return self._run(self._client.context_stats())

    def close(self) -> None:
        """Close the wrapped client and stop the loop thread."""
        try:
            self._run(self._client.aclose())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            self._loop.close()

    def __enter__(self) -> "SyncClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
