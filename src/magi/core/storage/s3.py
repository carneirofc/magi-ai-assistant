"""S3-compatible object store — a thin, synchronous boto3 wrapper.

One bucket, flat keys. The store knows nothing about *scope* or *users*: it puts,
gets, presigns, lists and probes by raw key. The scoping policy (which key prefix
a user's files live under) is the tools' job, exactly like `MemoryManager` layers
scope over the dumb `FileMemoryStore`.

boto3 is imported lazily inside `_client()` so the base install need not carry it
(it's the optional `s3` extra). All calls are blocking; the async tools wrap them
in `asyncio.to_thread`, so this module stays plain and testable.

Backends: any S3 API works — AWS S3 (leave `endpoint_url` None, set a region) or
a local S3-compatible server like RustFS / MinIO (set `endpoint_url`). Errors are
normalised to `StorageError` so callers don't import botocore exception types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from agno.utils.log import log_info, log_warning

from magi.core.config import config

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mypy_boto3_s3 import S3Client


class StorageError(RuntimeError):
    """Any object-store failure, normalised away from botocore's exception zoo."""


@dataclass(frozen=True)
class StoredObject:
    """The outcome of a successful put: where it landed and what it is."""

    key: str
    size: int
    content_type: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ObjectInfo:
    """One object as seen by a listing (metadata merged in from a HEAD)."""

    key: str
    size: int
    content_type: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class S3Store:
    """A single S3 bucket, addressed by raw key. Scope-agnostic and model-free."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None,
        region: str,
        access_key_id: str | None,
        secret_access_key: str | None,
        presign_expiry: int = 3600,
    ):
        self.bucket = bucket
        self._endpoint_url = endpoint_url
        self._region = region
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self.presign_expiry = presign_expiry
        self._client_cached: Optional["S3Client"] = None

    # --- client ------------------------------------------------------------
    def _client(self) -> "S3Client":
        """Build (once) and return the boto3 S3 client.

        Lazy so importing this module never requires boto3; raises a clear
        `StorageError` when the optional extra is missing.
        """
        if self._client_cached is not None:
            return self._client_cached
        try:
            import boto3  # noqa: PLC0415 — optional dependency, imported on demand.
        except ImportError as exc:  # pragma: no cover - exercised via the factory.
            raise StorageError(
                "object storage needs boto3 — install the optional extra: "
                "`uv sync --extra s3`"
            ) from exc
        self._client_cached = boto3.client(
            "s3",
            endpoint_url=self._endpoint_url,
            region_name=self._region,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
        )
        return self._client_cached

    # --- operations --------------------------------------------------------
    def ensure_bucket(self) -> None:
        """Create the bucket if it isn't there (idempotent; convenient for tests)."""
        client = self._client()
        try:
            client.head_bucket(Bucket=self.bucket)
            return
        except Exception:  # noqa: BLE001 — head fails for missing OR forbidden; try create.
            pass
        try:
            client.create_bucket(Bucket=self.bucket)
            log_info(f"storage: created bucket {self.bucket!r}")
        except Exception as exc:  # noqa: BLE001
            # A concurrent create or "already owned by you" is fine; anything else surfaces.
            name = type(exc).__name__
            if "BucketAlreadyOwnedByYou" in name or "BucketAlreadyExists" in name:
                return
            raise StorageError(f"could not ensure bucket {self.bucket!r}: {exc}") from exc

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> StoredObject:
        """Store `data` at `key`. Metadata rides as S3 user metadata (x-amz-meta-*)."""
        client = self._client()
        extra: dict[str, object] = {}
        if content_type:
            extra["ContentType"] = content_type
        if metadata:
            # S3 user-metadata values must be ASCII header-safe; keep it simple.
            extra["Metadata"] = {k: _header_safe(v) for k, v in metadata.items()}
        try:
            client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"put failed for {key!r}: {exc}") from exc
        return StoredObject(
            key=key, size=len(data), content_type=content_type, metadata=metadata or {}
        )

    def get_bytes(self, key: str) -> tuple[bytes, str | None, dict[str, str]]:
        """Fetch the object body, its content-type, and its user metadata."""
        client = self._client()
        try:
            resp = client.get_object(Bucket=self.bucket, Key=key)
            body = resp["Body"].read()
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"get failed for {key!r}: {exc}") from exc
        ctype = resp.get("ContentType")
        metadata = dict(resp.get("Metadata") or {})
        return body, ctype, metadata

    def exists(self, key: str) -> bool:
        """Whether an object lives at `key`."""
        client = self._client()
        try:
            client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001 — 404/403 both mean "not usable here".
            return False

    def presigned_url(self, key: str, *, expires_in: int | None = None) -> str:
        """A time-limited GET URL for `key` (for files too big to attach inline)."""
        client = self._client()
        try:
            return client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in or self.presign_expiry,
            )
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"presign failed for {key!r}: {exc}") from exc

    def list(self, prefix: str, *, with_metadata: bool = True, max_keys: int = 100) -> list[ObjectInfo]:
        """List objects under `prefix`. HEADs each for metadata when asked."""
        client = self._client()
        try:
            resp = client.list_objects_v2(Bucket=self.bucket, Prefix=prefix, MaxKeys=max_keys)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"list failed for prefix {prefix!r}: {exc}") from exc
        out: list[ObjectInfo] = []
        for item in resp.get("Contents") or []:
            key = item["Key"]
            size = int(item.get("Size", 0))
            ctype: str | None = None
            metadata: dict[str, str] = {}
            if with_metadata:
                try:
                    head = client.head_object(Bucket=self.bucket, Key=key)
                    ctype = head.get("ContentType")
                    metadata = dict(head.get("Metadata") or {})
                except Exception:  # noqa: BLE001 — a HEAD race shouldn't drop the listing.
                    pass
            out.append(ObjectInfo(key=key, size=size, content_type=ctype, metadata=metadata))
        return out


def _header_safe(value: str) -> str:
    """Coerce a metadata value to an ASCII, single-line, header-safe string."""
    return value.encode("ascii", "replace").decode("ascii").replace("\n", " ").strip()[:512]


def build_s3_store_from_config() -> Optional[S3Store]:
    """Build the store from `config`, or `None` when storage is off / unbuildable.

    Returns `None` (with a warning) rather than raising, so a deployment that
    selects the S3 backend without installing boto3 — or before its RustFS is up —
    still boots; the storage tools simply aren't attached.
    """
    if not config.storage_enabled:
        return None
    try:
        import boto3  # noqa: F401, PLC0415 — presence probe; real client built lazily.
    except ImportError:
        log_warning(
            "storage: S3 backend selected but boto3 is not installed — storage tools "
            "disabled. Install the optional extra (`uv sync --extra s3`) or set "
            "storage_backend='local'."
        )
        return None
    store = S3Store(
        bucket=config.s3_bucket,
        endpoint_url=config.s3_endpoint_url,
        region=config.s3_region,
        access_key_id=config.s3_access_key_id,
        secret_access_key=config.s3_secret_access_key,
        presign_expiry=config.s3_presign_expiry,
    )
    log_info(
        f"storage: S3 store ready (bucket={config.s3_bucket}, "
        f"endpoint={config.s3_endpoint_url or 'aws'}, region={config.s3_region})"
    )
    return store
