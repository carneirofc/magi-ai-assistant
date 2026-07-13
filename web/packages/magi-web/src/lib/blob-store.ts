// Blob storage abstraction for chat media. Images (and other attachment bytes) are
// NOT stored inline in transcripts — they're written to a BlobStore and referenced by
// id (served back via /api/chat/blobs/<id>). Two interchangeable backends:
//
//   - LocalFileBlobStore — files under a directory (default: OS temp dir). The default.
//   - S3BlobStore        — an S3 / S3-compatible bucket (RustFS, MinIO, AWS).
//
// Selected by env (see `getBlobStore`). Blobs are content-addressed (sha256 of the
// bytes) so identical images dedupe to one object and ids are stable & safe to serve.
//
// Server-only: touches the filesystem / network — import from route handlers only.

import "server-only";

import { createHash } from "crypto";
import { promises as fs } from "fs";
import os from "os";
import path from "path";

import { getConfigValue } from "./runtime-config";

export interface StoredBlob {
  bytes: Buffer;
  mimeType: string;
}

/** A content-addressed byte store. `put` returns the blob's id (its content hash). */
export interface BlobStore {
  put(bytes: Buffer, mimeType: string): Promise<string>;
  get(id: string): Promise<StoredBlob | null>;
  delete(id: string): Promise<void>;
}

/** The content-address (sha256 hex) of some bytes — also the blob id. */
export function blobId(bytes: Buffer): string {
  return createHash("sha256").update(bytes).digest("hex");
}

/** Blob ids are sha256 hex; reject anything else so a crafted id can't traverse the
 * local store or hit unexpected S3 keys. */
export function isValidBlobId(id: string): boolean {
  return /^[a-f0-9]{64}$/.test(id);
}

const DEFAULT_MIME = "application/octet-stream";

// --- local filesystem backend ------------------------------------------------
class LocalFileBlobStore implements BlobStore {
  constructor(private readonly dir: string) {}

  private paths(id: string): { bin: string; meta: string } {
    return { bin: path.join(this.dir, id), meta: path.join(this.dir, `${id}.type`) };
  }

  async put(bytes: Buffer, mimeType: string): Promise<string> {
    const id = blobId(bytes);
    const { bin, meta } = this.paths(id);
    await fs.mkdir(this.dir, { recursive: true });
    // Content-addressed → identical bytes already on disk need no rewrite.
    try {
      await fs.access(bin);
    } catch {
      await fs.writeFile(bin, bytes);
    }
    await fs.writeFile(meta, mimeType || DEFAULT_MIME, "utf8");
    return id;
  }

  async get(id: string): Promise<StoredBlob | null> {
    if (!isValidBlobId(id)) return null;
    const { bin, meta } = this.paths(id);
    try {
      const bytes = await fs.readFile(bin);
      const mimeType = await fs.readFile(meta, "utf8").catch(() => DEFAULT_MIME);
      return { bytes, mimeType: mimeType || DEFAULT_MIME };
    } catch {
      return null;
    }
  }

  async delete(id: string): Promise<void> {
    if (!isValidBlobId(id)) return;
    const { bin, meta } = this.paths(id);
    await Promise.allSettled([fs.unlink(bin), fs.unlink(meta)]);
  }
}

// --- S3 / S3-compatible backend ----------------------------------------------
import {
  DeleteObjectCommand,
  GetObjectCommand,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";

class S3BlobStore implements BlobStore {
  private readonly client: S3Client;
  constructor(
    private readonly bucket: string,
    private readonly prefix: string,
    options: { endpoint?: string; region: string; accessKeyId?: string; secretAccessKey?: string },
  ) {
    this.client = new S3Client({
      region: options.region,
      ...(options.endpoint ? { endpoint: options.endpoint, forcePathStyle: true } : {}),
      ...(options.accessKeyId && options.secretAccessKey
        ? {
            credentials: {
              accessKeyId: options.accessKeyId,
              secretAccessKey: options.secretAccessKey,
            },
          }
        : {}),
    });
  }

  private key(id: string): string {
    return `${this.prefix}${id}`;
  }

  async put(bytes: Buffer, mimeType: string): Promise<string> {
    const id = blobId(bytes);
    await this.client.send(
      new PutObjectCommand({
        Bucket: this.bucket,
        Key: this.key(id),
        Body: bytes,
        ContentType: mimeType || DEFAULT_MIME,
      }),
    );
    return id;
  }

  async get(id: string): Promise<StoredBlob | null> {
    if (!isValidBlobId(id)) return null;
    try {
      const res = await this.client.send(
        new GetObjectCommand({ Bucket: this.bucket, Key: this.key(id) }),
      );
      if (!res.Body) return null;
      const bytes = Buffer.from(await res.Body.transformToByteArray());
      return { bytes, mimeType: res.ContentType || DEFAULT_MIME };
    } catch {
      return null;
    }
  }

  async delete(id: string): Promise<void> {
    if (!isValidBlobId(id)) return;
    try {
      await this.client.send(new DeleteObjectCommand({ Bucket: this.bucket, Key: this.key(id) }));
    } catch {
      /* already gone / unreachable */
    }
  }
}

// --- selection ---------------------------------------------------------------
let cached: BlobStore | undefined;

/** The configured blob store (memoized). S3 when `CHAT_BLOB_S3_BUCKET` is set,
 * otherwise a local-file store under `CHAT_BLOB_DIR` (default: OS temp dir). */
export function getBlobStore(): BlobStore {
  if (cached) return cached;
  const bucket = process.env.CHAT_BLOB_S3_BUCKET;
  if (bucket) {
    cached = new S3BlobStore(bucket, process.env.CHAT_BLOB_S3_PREFIX ?? "chat-blobs/", {
      endpoint: process.env.CHAT_BLOB_S3_ENDPOINT,
      region: process.env.CHAT_BLOB_S3_REGION ?? "us-east-1",
      // Reuse the backend's S3 credential env names (see core/config.py).
      accessKeyId: process.env.S3_ACCESS_KEY_ID,
      secretAccessKey: process.env.S3_SECRET_ACCESS_KEY,
    });
  } else {
    // Resolved through the runtime-config store (file override → CHAT_BLOB_DIR →
    // OS temp dir). Memoized here, so a Settings change is restart-required — the
    // editor flags this field. See runtime-config.ts.
    cached = new LocalFileBlobStore(
      getConfigValue("chatBlobDir") || path.join(os.tmpdir(), "magi-chat-blobs"),
    );
  }
  return cached;
}
