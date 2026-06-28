"""Object-storage tools — the model's durable file/image archive.

These let the lead treat an S3-compatible bucket as *memory for bytes*: stash a
file the user may want again, recall it later, list what's kept. They are the
byte-world sibling of the text memory tools (magi/agent/tools/memory.py) and share its
shape: `build_storage_tools(store, memory)` binds them to an injected `S3Store`
plus the `MemoryManager`, and every object lives under a key prefix scoped to the
current user — the channel sets that scope once per message, so the model calls
these with no ids.

Safety mirrors the media tool: a store only fetches a URL the user supplied or a
source tool registered this turn (the media allowlist), and never a private/
loopback host the model invented. Recall delivers the actual bytes as an
attachment via the media outbox (or a time-limited URL when it's too big to
attach) — the bucket is a private archive, not a public file host.
"""

import asyncio
from pathlib import Path
from typing import Annotated, Final, Optional
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from agno.tools import tool
from agno.utils.log import log_info, log_warning
from pydantic import BaseModel, Field

from magi.agent.tools.outputs import ToolOutput, fail, ok
from magi.core.media import is_media_url_allowed, stage_bytes
from magi.core.memory import MemoryManager
from magi.core.memory.adapters import slug
from magi.core.storage import ObjectInfo, S3Store, StorageError

# Upload cap (a stored archive can be larger than an inline attachment); recall
# attaches inline up to the channel-ish limit and otherwise hands back a URL.
_MAX_STORE_BYTES: Final[int] = 100 * 1024 * 1024
_MAX_ATTACH_BYTES: Final[int] = 50 * 1024 * 1024
_FETCH_TIMEOUT_S: Final[float] = 30.0
_HEADERS: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0 (compatible; AlyssaBot/1.0; +https://discord.com)"
}


class StoredFileData(BaseModel):
    reference: str | None = Field(default=None, description="Stable id to recall this file later.")
    filename: str | None = Field(default=None, description="Stored display filename, when known.")
    note: str | None = Field(default=None, description="Note kept alongside the file, if any.")
    content_type: str | None = Field(default=None, description="Detected MIME type, when known.")
    bytes: int | None = Field(default=None, description="Stored byte count, when known.")
    source_url: str | None = Field(default=None, description="URL the file was archived from.")


class RetrievedFileData(BaseModel):
    reference: str = Field(description="Reference that was recalled.")
    filename: str | None = Field(default=None, description="Recalled filename, when known.")
    kind: str | None = Field(default=None, description="Delivered media kind: image, audio, video, or file.")
    content_type: str | None = Field(default=None, description="Detected MIME type, when known.")
    bytes: int | None = Field(default=None, description="Recalled byte count, when known.")
    delivered: bool | None = Field(default=None, description="Whether the file was attached to the reply.")
    url: str | None = Field(default=None, description="Time-limited URL, when the file was too big to attach.")


class StoredFileEntry(BaseModel):
    reference: str = Field(description="Stable id to recall this file.")
    filename: str | None = Field(default=None, description="Stored display filename, when known.")
    note: str | None = Field(default=None, description="Note kept with the file, if any.")
    content_type: str | None = Field(default=None, description="MIME type, when known.")
    bytes: int = Field(description="Stored byte count.")


class StoredFileListData(BaseModel):
    files: list[StoredFileEntry] = Field(description="Files archived for the current user.")
    count: int = Field(description="How many files are archived.")


def _filename(url: str, content_type: str, explicit: Optional[str]) -> str:
    """A sensible filename: explicit > URL basename > a generic one."""
    if explicit:
        return explicit
    name = Path(urlparse(url).path).name
    if name and "." in name:
        return name
    import mimetypes

    ext = mimetypes.guess_extension(content_type) or ""
    return f"file{ext}"


def build_storage_tools(store: S3Store, memory: MemoryManager) -> list:
    """Return the object-storage tool set bound to `store` + `memory` (injected)."""

    def _prefix() -> str:
        """Key prefix for the current user's archive (scope set by the channel)."""
        return f"users/{slug(memory.scope().user_id)}/artifacts/"

    @tool(
        description="Archive a file or image to the user's durable private storage for later reference.",
        instructions=(
            "Use to keep a file the user may want again — a generated image, a document, a reference "
            "picture — beyond this conversation. Pass a URL the user supplied or that a source tool "
            "returned this turn; never invent, guess, or reuse a stale URL. Returns a short reference "
            "id; mention it so the file can be recalled later with retrieve_file. This is a private "
            "archive, not a public host — do not present it as an upload/CDN link."
        ),
        show_result=True,
    )
    async def store_file(
        source_url: Annotated[
            str,
            Field(min_length=8, description="Direct HTTP(S) URL of the file to archive."),
        ],
        filename: Annotated[
            Optional[str],
            Field(default=None, description="Optional display filename to store with the file."),
        ] = None,
        note: Annotated[
            Optional[str],
            Field(default=None, description="Optional short note describing what this file is."),
        ] = None,
    ) -> ToolOutput[StoredFileData]:
        """Fetch a file from a URL and keep it in the current user's durable, private
        object storage, returning a stable reference id to recall it later.

        Use when the user (or your own judgement) wants a file kept for the future:
        an image you delivered, a document, a reference picture. The URL must come
        from the user or a successful source-specific tool result this turn — never
        a guessed, reconstructed, or stale URL. `filename` and `note` are optional
        metadata stored alongside the bytes (the note helps you recognise the file
        in list_files later). Returns the reference id and stored details, or a
        readable error.
        """
        url = (source_url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return fail(f"Refusing to fetch non-http(s) URL: {url!r}", StoredFileData(source_url=url))
        if not is_media_url_allowed(url):
            return fail(
                "Refusing to archive an unsourced URL. Use a URL from the user or from a "
                "successful source-specific tool result in this turn.",
                StoredFileData(source_url=url),
            )

        try:
            async with httpx.AsyncClient(
                timeout=_FETCH_TIMEOUT_S, follow_redirects=True, headers=_HEADERS
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return fail(
                f"Could not fetch the file: HTTP {exc.response.status_code} for {url}",
                StoredFileData(source_url=url),
            )
        except httpx.HTTPError as exc:
            return fail(f"Could not fetch the file from {url}: {exc}", StoredFileData(source_url=url))

        data = resp.content
        if not data:
            return fail(f"The URL returned an empty body: {url}", StoredFileData(source_url=url))
        if len(data) > _MAX_STORE_BYTES:
            return fail(
                f"File is too large to archive ({len(data)} bytes; limit {_MAX_STORE_BYTES}).",
                StoredFileData(source_url=url, bytes=len(data)),
            )

        ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        ctype = ctype or "application/octet-stream"
        name = _filename(url, ctype, filename)
        reference = uuid4().hex[:12]
        key = f"{_prefix()}{reference}"
        metadata = {"filename": name, "source-url": url}
        if note:
            metadata["note"] = note

        try:
            await asyncio.to_thread(
                store.put_bytes, key, data, content_type=ctype, metadata=metadata
            )
        except StorageError as exc:
            log_warning(f"store_file: {exc}")
            return fail(f"Could not archive the file: {exc}", StoredFileData(source_url=url))

        log_info(
            f"storage: archived {reference} ('{name}', {len(data)} bytes, {ctype}) "
            f"for user {memory.scope().user_id}"
        )
        return ok(
            f"Archived '{name}' ({ctype}, {len(data)} bytes) as reference {reference}. "
            "Recall it later with retrieve_file using that reference.",
            StoredFileData(
                reference=reference,
                filename=name,
                note=note,
                content_type=ctype,
                bytes=len(data),
                source_url=url,
            ),
        )

    @tool(
        description="Recall a previously archived file by its reference and deliver it to the user.",
        instructions=(
            "Use with a reference id from store_file or list_files to fetch a file the user archived "
            "earlier. The file is attached to your reply; if it's too large to attach, a time-limited "
            "URL is returned instead. Do not guess references — list_files shows what's available."
        ),
        show_result=True,
    )
    async def retrieve_file(
        reference: Annotated[
            str,
            Field(min_length=1, description="Reference id returned by store_file or list_files."),
        ],
    ) -> ToolOutput[RetrievedFileData]:
        """Recall a file from the current user's durable storage by its reference id
        and deliver it to the user as a real attachment.

        Use a reference from store_file or list_files. The bytes are fetched and
        attached to your reply (image, audio, video, or document) — not loaded into
        your own context. If the file is too large to attach, a time-limited URL is
        returned instead; share that. Returns a confirmation or a readable error
        (for example, an unknown reference).
        """
        ref = (reference or "").strip()
        key = f"{_prefix()}{ref}"
        try:
            data, ctype, metadata = await asyncio.to_thread(store.get_bytes, key)
        except StorageError as exc:
            log_warning(f"retrieve_file: {exc}")
            return fail(
                f"No archived file found for reference {ref!r} (or it could not be read).",
                RetrievedFileData(reference=ref, delivered=False),
            )

        name = metadata.get("filename") or ref
        if len(data) > _MAX_ATTACH_BYTES:
            try:
                link = await asyncio.to_thread(store.presigned_url, key)
            except StorageError as exc:
                return fail(f"File is too large to attach and a link could not be made: {exc}",
                            RetrievedFileData(reference=ref, filename=name, bytes=len(data)))
            return ok(
                f"'{name}' is too large to attach ({len(data)} bytes); here is a time-limited link.",
                RetrievedFileData(
                    reference=ref, filename=name, content_type=ctype, bytes=len(data),
                    delivered=False, url=link,
                ),
            )

        kind, staged = stage_bytes(data, ctype, name)
        if not staged:
            # No outbox open (bare run) — fall back to a link so we stay honest.
            try:
                link = await asyncio.to_thread(store.presigned_url, key)
            except StorageError:
                link = None
            return fail(
                "File delivery is not available in this run. " + (f"Link: {link}" if link else ""),
                RetrievedFileData(reference=ref, filename=name, content_type=ctype,
                                  bytes=len(data), delivered=False, url=link),
            )

        log_info(f"storage: recalled {ref} ('{name}', {len(data)} bytes, {kind}) for delivery")
        return ok(
            f"Attached the {kind} '{name}' ({len(data)} bytes) to your reply from the user's archive.",
            RetrievedFileData(
                reference=ref, filename=name, kind=kind, content_type=ctype,
                bytes=len(data), delivered=True,
            ),
        )

    @tool(
        description="List the files the current user has archived in durable storage.",
        instructions="Use to see what files are kept for the user, with their reference ids and notes. Takes no arguments.",
        show_result=True,
    )
    async def list_files() -> ToolOutput[StoredFileListData]:
        """List the files archived for the current user, with reference ids and notes.

        Returns each file's reference (use it with retrieve_file), stored filename,
        note, type, and size. Empty when nothing has been archived yet.
        """
        prefix = _prefix()
        try:
            objects: list[ObjectInfo] = await asyncio.to_thread(store.list, prefix)
        except StorageError as exc:
            log_warning(f"list_files: {exc}")
            return fail(f"Could not list archived files: {exc}", StoredFileListData(files=[], count=0))

        entries = [
            StoredFileEntry(
                reference=obj.key[len(prefix):] or obj.key,
                filename=obj.metadata.get("filename"),
                note=obj.metadata.get("note"),
                content_type=obj.content_type,
                bytes=obj.size,
            )
            for obj in objects
        ]
        msg = (
            f"{len(entries)} archived file(s) for this user."
            if entries
            else "No files archived for this user yet."
        )
        return ok(msg, StoredFileListData(files=entries, count=len(entries)))

    return [store_file, retrieve_file, list_files]
