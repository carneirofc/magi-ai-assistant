"""Object storage — an S3-compatible durable file/image archive.

`magi/core/memory` keeps *text* the model decides to remember; this keeps *bytes*. The
two are siblings: both are deliberate, scope-aware memory the model writes on
purpose, never framework auto-extraction. A file the user may want again — a
generated image, a document, a reference picture — is stashed here under the
current user's scope and recalled later by reference.

This layer is model-free and side-effect-light: `S3Store` wraps a boto3 client
(lazy-imported so the base install stays lean), and `build_s3_store_from_config`
constructs it from `config`, degrading to `None` when storage is off or boto3 is
absent. The model-facing tools (magi/agent/tools/storage.py) bind to a store + the
memory manager for scope.
"""

from magi.core.storage.s3 import (
    ObjectInfo,
    S3Store,
    StorageError,
    StoredObject,
    build_s3_store_from_config,
)

__all__ = [
    "ObjectInfo",
    "S3Store",
    "StorageError",
    "StoredObject",
    "build_s3_store_from_config",
]
