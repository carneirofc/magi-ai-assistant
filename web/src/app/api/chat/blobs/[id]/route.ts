// Serves a chat media blob by id from the configured blob store (local file or S3;
// see blob-store.ts). Blobs are content-addressed and immutable, so they cache hard.
// Referenced from transcripts as /api/chat/blobs/<id> once media is offloaded
// (chat-media-offload.ts).

import { getBlobStore, isValidBlobId } from "@/lib/blob-store";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Ctx = { params: Promise<{ id: string }> };

export async function GET(_req: Request, { params }: Ctx) {
  const { id } = await params;
  if (!isValidBlobId(id)) return new Response("not found", { status: 404 });

  const blob = await getBlobStore().get(id);
  if (!blob) return new Response("not found", { status: 404 });

  return new Response(new Uint8Array(blob.bytes), {
    status: 200,
    headers: {
      "Content-Type": blob.mimeType,
      "Content-Length": String(blob.bytes.length),
      // Content-addressed by hash → safe to cache forever.
      "Cache-Control": "public, max-age=31536000, immutable",
    },
  });
}
