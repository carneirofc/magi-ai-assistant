"""Local filesystem object store — the no-server sibling of `S3Store`.

Same contract as `S3Store` (put/get/exists/presign/list by raw key), but the
bytes land on disk under a root directory instead of in a bucket. This is the
default-friendly backend: it needs no boto3, no S3 server, no credentials — just
a writable folder — so a deployment can give the model a durable byte archive
(generated images, documents, reference pictures the user wants kept) with
nothing to stand up. It is the byte-world peer of `FileMemoryStore`, which keeps
the model's *text* memory as plain files in the same spirit.

Layout — a key maps straight to a path under `root`, with a JSON sidecar holding
the content-type and user metadata S3 would carry as `x-amz-meta-*`:

    <root>/users/<user>/artifacts/<reference>            # the bytes
    <root>/users/<user>/artifacts/<reference>.meta.json  # {content_type, metadata, size}

Keys use `/` separators (set by the tools, exactly as for S3); they are resolved
against `root` and validated to stay inside it, so a crafted key can't escape the
archive. `presigned_url` has no signing server to call, so it returns a `file://`
URL to the on-disk path — honest for a local archive, used only when a recalled
file is too big to attach inline. Errors are normalised to `StorageError` so the
model-facing tools (magi/agent/tools/storage.py) treat both backends identically.
"""

from __future__ import annotations

import json
from pathlib import Path

from agno.utils.log import log_info

from magi.core.config import Config
from magi.core.storage.s3 import ObjectInfo, StorageError, StoredObject

# Sidecar suffix for a key's content-type + metadata. Kept distinct so listings
# can skip the sidecars and never mistake one for an archived object.
_META_SUFFIX = ".meta.json"


class LocalStore:
    """A directory tree addressed by raw key. Scope-agnostic and model-free.

    Mirrors `S3Store`'s surface so `build_storage_tools` binds to either without
    knowing which. All calls are blocking; the async tools wrap them in
    `asyncio.to_thread`, so this stays plain and testable.
    """

    def __init__(self, root: Path | str, *, presign_expiry: int = 3600):
        self.root = Path(root).resolve()
        # Carried for parity with S3Store; file:// URLs don't actually expire.
        self.presign_expiry = presign_expiry

    # --- paths -------------------------------------------------------------
    def _path(self, key: str) -> Path:
        """Resolve `key` to a path under `root`, rejecting traversal.

        Keys are `/`-joined segments (the tools build them); empty segments and
        `.`/`..` are refused so a key can never address anything outside `root`.
        """
        parts = [p for p in str(key).split("/") if p not in ("", ".")]
        if not parts or any(p == ".." for p in parts):
            raise StorageError(f"unsafe storage key: {key!r}")
        path = self.root.joinpath(*parts)
        # Defensive: even with the checks above, confirm containment after resolve.
        try:
            path.resolve().relative_to(self.root)
        except ValueError as exc:  # pragma: no cover - guarded by the parts check.
            raise StorageError(f"storage key escapes root: {key!r}") from exc
        return path

    def _meta_path(self, path: Path) -> Path:
        return path.with_name(path.name + _META_SUFFIX)

    # --- operations --------------------------------------------------------
    def ensure_bucket(self) -> None:
        """Create the root directory if absent (idempotent; the bucket analogue)."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            log_info(f"storage: local archive ready at {self.root}")
        except OSError as exc:
            raise StorageError(f"could not create storage root {self.root}: {exc}") from exc

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> StoredObject:
        """Write `data` at `key`, with a JSON sidecar for content-type + metadata."""
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            sidecar = {
                "content_type": content_type,
                "metadata": dict(metadata or {}),
                "size": len(data),
            }
            self._meta_path(path).write_text(
                json.dumps(sidecar, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            raise StorageError(f"put failed for {key!r}: {exc}") from exc
        return StoredObject(
            key=key, size=len(data), content_type=content_type, metadata=metadata or {}
        )

    def get_bytes(self, key: str) -> tuple[bytes, str | None, dict[str, str]]:
        """Fetch the object body, its content-type, and its user metadata."""
        path = self._path(key)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise StorageError(f"get failed for {key!r}: {exc}") from exc
        ctype, metadata = self._read_sidecar(path)
        return data, ctype, metadata

    def exists(self, key: str) -> bool:
        """Whether an object lives at `key`."""
        try:
            return self._path(key).is_file()
        except StorageError:
            return False

    def delete_bytes(self, key: str) -> None:
        """Remove the blob at `key` and its sidecar (idempotent — absent is fine)."""
        path = self._path(key)
        try:
            path.unlink(missing_ok=True)
            self._meta_path(path).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(f"delete failed for {key!r}: {exc}") from exc

    def presigned_url(self, key: str, *, expires_in: int | None = None) -> str:
        """A `file://` URL to the on-disk path (no signing server for local).

        Handed back when a recalled file is too big to attach inline; for a local
        archive the link is the file's own path rather than a time-limited URL.
        """
        path = self._path(key)
        if not path.is_file():
            raise StorageError(f"presign failed for {key!r}: no such object")
        return path.resolve().as_uri()

    def list(
        self, prefix: str, *, with_metadata: bool = True, max_keys: int = 100
    ) -> list[ObjectInfo]:
        """List objects whose key starts with `prefix` (sidecars excluded)."""
        base = self._path(prefix) if prefix else self.root
        # A prefix may name a directory (the common case) or a partial filename;
        # scan the nearest existing directory and filter by the full key prefix.
        scan_dir = base if base.is_dir() else base.parent
        if not scan_dir.exists():
            return []
        out: list[ObjectInfo] = []
        try:
            candidates = sorted(p for p in scan_dir.rglob("*") if p.is_file())
        except OSError as exc:
            raise StorageError(f"list failed for prefix {prefix!r}: {exc}") from exc
        for path in candidates:
            if path.name.endswith(_META_SUFFIX):
                continue
            key = path.relative_to(self.root).as_posix()
            if not key.startswith(prefix):
                continue
            ctype: str | None = None
            metadata: dict[str, str] = {}
            if with_metadata:
                ctype, metadata = self._read_sidecar(path)
            out.append(
                ObjectInfo(
                    key=key, size=path.stat().st_size, content_type=ctype, metadata=metadata
                )
            )
            if len(out) >= max_keys:
                break
        return out

    # --- internals ---------------------------------------------------------
    def _read_sidecar(self, path: Path) -> tuple[str | None, dict[str, str]]:
        """Read a blob's `.meta.json` sidecar; tolerate a missing/corrupt one."""
        try:
            raw = json.loads(self._meta_path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, {}
        ctype = raw.get("content_type")
        metadata = {str(k): str(v) for k, v in (raw.get("metadata") or {}).items()}
        return ctype, metadata


def build_local_store_from_config(config: Config) -> LocalStore:
    """Build the local store from `config` and ensure its root exists."""
    store = LocalStore(config.storage_local_dir, presign_expiry=config.s3_presign_expiry)
    store.ensure_bucket()
    log_info(f"storage: local store ready (dir={config.storage_local_dir})")
    return store
