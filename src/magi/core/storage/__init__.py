"""Object storage — a durable file/image archive the model uses as memory.

`magi/core/memory` keeps *text* the model decides to remember; this keeps *bytes*.
The two are siblings: both are deliberate, scope-aware memory the model writes on
purpose, never framework auto-extraction. A file the user may want again — a
generated image, a document, a reference picture — is stashed here under the
current user's scope and recalled later by reference.

Two interchangeable backends behind one duck-typed surface (put/get/exists/
presign/list by raw key), selected by `config.storage_backend`:

  - `LocalStore`  — bytes on the filesystem (no server, no boto3, no creds); the
                    zero-setup default. See `local.py`.
  - `S3Store`     — any S3-compatible bucket (AWS S3, RustFS, MinIO). See `s3.py`.

`build_object_store_from_config()` is the single entry point: it returns the
configured backend, or `None` when storage is off / unbuildable, so a deployment
without it still boots cleanly. The model-facing tools (magi/agent/tools/storage.py)
bind to whichever store it hands back plus the memory manager for scope.
"""

from typing import Optional, Union

from agno.utils.log import log_warning

from magi.core.config import config
from magi.core.storage.local import LocalStore, build_local_store_from_config
from magi.core.storage.s3 import (
    ObjectInfo,
    S3Store,
    StorageError,
    StoredObject,
    build_s3_store_from_config,
    s3_store_from_config,
)

__all__ = [
    "LocalStore",
    "ObjectInfo",
    "S3Store",
    "StorageError",
    "StoredObject",
    "build_local_store_from_config",
    "build_object_store",
    "build_object_store_from_config",
    "build_s3_store_from_config",
]


def build_object_store(
    backend: Optional[str] = None,
) -> Optional[Union[LocalStore, S3Store]]:
    """Build the object store for `backend` (default `config.storage_backend`),
    *ungated* by `storage_enabled`.

    Dispatches on the backend name: "local" => filesystem (always buildable), "s3"
    => S3-compatible bucket (degrades to `None` without boto3). Use this when the
    caller owns the gate (the item archive has its own flag); use
    `build_object_store_from_config` for the model's file tools, which gate on
    `storage_enabled`. Never raises — an unknown/down backend returns `None`.
    """
    name = (backend or config.storage_backend or "local").strip().lower()
    if name == "local":
        return build_local_store_from_config()
    if name == "s3":
        return s3_store_from_config()
    log_warning(
        f"storage: unknown storage_backend {name!r} "
        "(expected 'local' or 's3') — storage tools disabled"
    )
    return None


def build_object_store_from_config() -> Optional[Union[LocalStore, S3Store]]:
    """The configured object store, or `None` when storage is off / unbuildable.

    Honors the model-file-archive gate (`storage_enabled`), then dispatches on
    `config.storage_backend`. Never raises — a misconfigured or down backend just
    leaves the storage tools unattached so the rest of the app still boots.
    """
    if not config.storage_enabled:
        return None
    return build_object_store(config.storage_backend)
