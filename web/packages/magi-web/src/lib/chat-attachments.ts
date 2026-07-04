// Composer attachment support for the chat console. assistant-ui's attachment
// adapters turn a picked File into message content parts the runtime carries on
// the user turn; our chat adapter (chat-adapter.ts) then serializes those parts
// into the chat-api's `images[]` / `files[]` wire fields.
//
// Images go through a custom adapter that reads the bytes to a data URL up front,
// so the attachment carries its `image` part while still pending — the composer
// and the sent message both show an inline preview, not a generic chip. Everything
// else goes through a small custom adapter that base64s the bytes into a `file`
// part — the backend accepts arbitrary inbound files (channels/api.py `InboundFile`).

import {
  CompositeAttachmentAdapter,
  type AttachmentAdapter,
  type CompleteAttachment,
  type PendingAttachment,
} from "@assistant-ui/react";

/** The full `data:<mime>;base64,<payload>` URL of a File, read in the browser. */
function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("failed to read file"));
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
    reader.readAsDataURL(file);
  });
}

/** The raw base64 payload of a File (no `data:` prefix). */
async function fileToBase64(file: File): Promise<string> {
  const url = await fileToDataUrl(file);
  const comma = url.indexOf(",");
  return comma === -1 ? url : url.slice(comma + 1);
}

/** Handles image files: reads the bytes to a data URL up front (in `add`) so the
 * attachment carries its `image` part while still pending — the composer and the
 * sent message both render an inline preview, not a generic chip. */
function createImageAttachmentAdapter(): AttachmentAdapter {
  return {
    accept: "image/*",
    async add({ file }): Promise<PendingAttachment> {
      const image = await fileToDataUrl(file);
      return {
        id: file.name,
        type: "image",
        name: file.name,
        contentType: file.type,
        file,
        content: [{ type: "image", image }],
        status: { type: "requires-action", reason: "composer-send" },
      };
    },
    async send(attachment): Promise<CompleteAttachment> {
      // Bytes were already read in `add`; reuse the preview part as the sent content.
      const image =
        attachment.content?.find((p) => p.type === "image")?.image ??
        (await fileToDataUrl(attachment.file));
      return {
        ...attachment,
        status: { type: "complete" },
        content: [{ type: "image", image }],
      };
    },
    async remove(): Promise<void> {
      // Nothing to clean up — the data URL lives only in memory.
    },
  };
}

/** Handles any non-image file: base64s it into a `file` content part. Declared
 * with the `"*"` wildcard accept, so in the composite it must be LAST (it catches
 * everything the image adapter didn't). */
function createFileAttachmentAdapter(): AttachmentAdapter {
  return {
    accept: "*",
    async add({ file }): Promise<PendingAttachment> {
      return {
        id: file.name,
        type: "file",
        name: file.name,
        contentType: file.type,
        file,
        status: { type: "requires-action", reason: "composer-send" },
      };
    },
    async send(attachment): Promise<CompleteAttachment> {
      const data = await fileToBase64(attachment.file);
      return {
        ...attachment,
        status: { type: "complete" },
        content: [
          {
            type: "file",
            data,
            mimeType: attachment.file.type || "application/octet-stream",
            filename: attachment.file.name,
          },
        ],
      };
    },
    async remove(): Promise<void> {
      // Nothing to clean up — bytes only materialize in `send`.
    },
  };
}

/** The console's attachment adapter: images inline (with preview), all other
 * files as base64 `file` parts. The wildcard file adapter must be last. */
export function createChatAttachmentAdapter(): AttachmentAdapter {
  return new CompositeAttachmentAdapter([
    createImageAttachmentAdapter(),
    createFileAttachmentAdapter(),
  ]);
}
