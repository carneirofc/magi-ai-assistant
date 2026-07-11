"use client";

// Edit the global bot identity: display name, description, profile picture, and
// the mood expression pack. The name/description save via the admin BFF with
// the optimistic-concurrency version; the picture and each mood's expression
// upload/clear on their own routes. Every write returns the new version (a hash
// over fields + all picture bytes), which also busts the previews.
//
// The expression pack is the companion UI's face: one portrait per mood in the
// deployment's vocabulary (identity.moods), swapped by the streamed mood. The
// `neutral` expression IS the profile picture above, so the grid offers a slot
// for every other mood, plus any uploaded mood that later left the vocabulary.

import { useRef, useState } from "react";
import { OutlineButton, StatusMessage, TextAreaInput, TextInput } from "@carneirofc/ui";

import type { AdminExpression, AdminIdentity } from "../lib/admin-api";

// Guard against an accidental multi-MB upload replayed to the model every turn.
const MAX_AVATAR_BYTES = 4 * 1024 * 1024;

// Strip the `data:<mime>;base64,` prefix a FileReader data URL carries.
function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result);
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function CameraIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="22"
      height="22"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M4 8a2 2 0 0 1 2-2h1.2l1-1.6a1 1 0 0 1 .85-.4h5.9a1 1 0 0 1 .85.4l1 1.6H18a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z" />
      <circle cx="12" cy="12.5" r="3.2" />
    </svg>
  );
}

export function IdentityEditor({ initial }: { initial: AdminIdentity }) {
  const [displayName, setDisplayName] = useState(initial.display_name);
  const [description, setDescription] = useState(initial.description);
  const [version, setVersion] = useState(initial.version);
  const [hasAvatar, setHasAvatar] = useState(initial.has_avatar);
  const [expressions, setExpressions] = useState<Record<string, AdminExpression>>(
    initial.expressions ?? {},
  );
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // True while a file is dragged over the avatar, so it can show a drop affordance.
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  function applyIdentity(next: AdminIdentity) {
    setVersion(next.version);
    setHasAvatar(next.has_avatar);
    setDisplayName(next.display_name);
    setDescription(next.description);
    setExpressions(next.expressions ?? {});
  }

  function explain(status: number): string {
    if (status === 409) return "Changed elsewhere since you loaded — reload the page.";
    if (status === 422) return "That image couldn't be used (unsupported type or corrupt).";
    return `Request failed (${status}).`;
  }

  async function saveFields() {
    setError(null);
    setSaved(false);
    setBusy(true);
    const res = await fetch("/api/admin/identity", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        display_name: displayName,
        description,
        expectedVersion: version,
      }),
    });
    setBusy(false);
    if (res.ok) {
      applyIdentity((await res.json()) as AdminIdentity);
      setDirty(false);
      setSaved(true);
      return;
    }
    setError(explain(res.status));
  }

  async function uploadAvatar(file: File) {
    setError(null);
    setSaved(false);
    if (!file.type.startsWith("image/")) {
      setError("Pick an image file.");
      return;
    }
    if (file.size > MAX_AVATAR_BYTES) {
      setError("Image is too large (max 4 MB).");
      return;
    }
    setBusy(true);
    const dataBase64 = await fileToBase64(file);
    const res = await fetch("/api/admin/identity/avatar", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        data_base64: dataBase64,
        mime_type: file.type,
        filename: file.name,
        expectedVersion: version,
      }),
    });
    setBusy(false);
    if (res.ok) {
      applyIdentity((await res.json()) as AdminIdentity);
      setSaved(true);
      return;
    }
    setError(explain(res.status));
  }

  async function removeAvatar() {
    setError(null);
    setSaved(false);
    setBusy(true);
    const res = await fetch(
      `/api/admin/identity/avatar?expected_version=${encodeURIComponent(version)}`,
      { method: "DELETE" },
    );
    setBusy(false);
    if (res.ok) {
      applyIdentity((await res.json()) as AdminIdentity);
      setSaved(true);
      return;
    }
    setError(explain(res.status));
  }

  async function uploadExpression(mood: string, file: File) {
    setError(null);
    setSaved(false);
    if (!file.type.startsWith("image/")) {
      setError("Pick an image file.");
      return;
    }
    if (file.size > MAX_AVATAR_BYTES) {
      setError("Image is too large (max 4 MB).");
      return;
    }
    setBusy(true);
    const dataBase64 = await fileToBase64(file);
    const res = await fetch(`/api/admin/identity/expressions/${encodeURIComponent(mood)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        data_base64: dataBase64,
        mime_type: file.type,
        filename: file.name,
        expectedVersion: version,
      }),
    });
    setBusy(false);
    if (res.ok) {
      applyIdentity((await res.json()) as AdminIdentity);
      setSaved(true);
      return;
    }
    setError(explain(res.status));
  }

  async function removeExpression(mood: string) {
    setError(null);
    setSaved(false);
    setBusy(true);
    const res = await fetch(
      `/api/admin/identity/expressions/${encodeURIComponent(mood)}?expected_version=${encodeURIComponent(version)}`,
      { method: "DELETE" },
    );
    setBusy(false);
    if (res.ok) {
      applyIdentity((await res.json()) as AdminIdentity);
      setSaved(true);
      return;
    }
    setError(explain(res.status));
  }

  // Version is part of the query so a new upload/clear reloads the <img>.
  const avatarSrc = hasAvatar ? `/api/admin/identity/avatar?v=${encodeURIComponent(version)}` : null;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start gap-5">
        <div className="flex flex-col items-center gap-2">
          {/* The avatar itself is the primary control: click or drop an image to
              set it, a hover/drag overlay spells that out, and a corner ✕ clears
              it. Explicit buttons below cover keyboard/discoverability. */}
          <div
            role="button"
            tabIndex={0}
            aria-label={hasAvatar ? "Replace profile picture" : "Upload profile picture"}
            aria-disabled={busy}
            onClick={() => {
              if (!busy) fileInput.current?.click();
            }}
            onKeyDown={(e) => {
              if ((e.key === "Enter" || e.key === " ") && !busy) {
                e.preventDefault();
                fileInput.current?.click();
              }
            }}
            onDragOver={(e) => {
              e.preventDefault();
              if (!busy) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const file = e.dataTransfer.files?.[0];
              if (file && !busy) void uploadAvatar(file);
            }}
            className={`group relative grid h-28 w-28 cursor-pointer place-items-center overflow-hidden rounded-full border-2 bg-[color:var(--ui-bg-soft)] text-3xl text-[color:var(--ui-ink-accent)] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-[color:var(--ui-border-active)] ${
              dragging ? "border-dashed border-[color:var(--ui-border-active)]" : "border-ui"
            } ${busy ? "cursor-not-allowed opacity-70" : ""}`}
          >
            {avatarSrc ? (
              // eslint-disable-next-line @next/next/no-img-element -- BFF-served, dynamic; no loader needed
              <img src={avatarSrc} alt="Profile picture" className="h-full w-full object-cover" />
            ) : (
              <span aria-hidden>{(displayName.trim()[0] || "M").toUpperCase()}</span>
            )}
            <div
              className={`pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-1 bg-black/55 text-white transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 ${
                dragging ? "opacity-100" : "opacity-0"
              }`}
            >
              <CameraIcon />
              <span className="text-ui-2xs font-medium">
                {dragging ? "Drop image" : hasAvatar ? "Replace" : "Upload"}
              </span>
            </div>
            {hasAvatar && !busy ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  void removeAvatar();
                }}
                title="Remove profile picture"
                aria-label="Remove profile picture"
                className="absolute right-1 top-1 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-ui bg-[color:var(--ui-bg)] text-ui-xs text-[color:var(--ui-ink-subtle)] opacity-0 transition-opacity hover:text-[color:var(--ui-ink-danger)] group-hover:opacity-100 group-focus-within:opacity-100"
              >
                ✕
              </button>
            ) : null}
          </div>
          <span className="text-ui-2xs text-[color:var(--ui-ink-subtle)]">
            {busy ? "Working…" : "PNG, JPG, GIF, or WebP · max 4 MB"}
          </span>
          <input
            ref={fileInput}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void uploadAvatar(file);
              e.target.value = ""; // let the same file re-trigger onChange
            }}
          />
        </div>

        <div className="flex min-w-[16rem] flex-1 flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
              Display name
            </span>
            <TextInput
              value={displayName}
              onChange={(e) => {
                setDisplayName(e.target.value);
                setDirty(true);
                setSaved(false);
              }}
              placeholder="e.g. Alyssa"
              className="max-w-sm"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
              Description
            </span>
            <TextAreaInput
              value={description}
              onChange={(e) => {
                setDescription(e.target.value);
                setDirty(true);
                setSaved(false);
              }}
              rows={5}
              placeholder="How the bot presents itself — tone, background, anything it should know about who it is."
            />
          </label>
        </div>
      </div>

      {/* The expression pack: one portrait per vocabulary mood (plus any uploaded
          mood no longer in the vocabulary). `neutral` is the profile picture
          above, so it gets no slot here. Uploads apply immediately, like the
          avatar. */}
      {(() => {
        const slots = Array.from(
          new Set([...(initial.moods ?? []), ...Object.keys(expressions)]),
        ).filter((mood) => mood !== "neutral");
        if (slots.length === 0) return null;
        return (
          <div className="flex flex-col gap-2">
            <span className="text-ui-2xs font-semibold uppercase tracking-wide text-[color:var(--ui-ink-subtle)]">
              Expressions
            </span>
            <p className="text-ui-xs text-[color:var(--ui-ink-muted)]">
              One portrait per mood — the companion surface swaps between these as the
              streamed mood changes. The profile picture above is the{" "}
              <code className="font-mono">neutral</code> face; moods without a portrait
              fall back to it.
            </p>
            <div className="flex flex-wrap gap-3">
              {slots.map((mood) => (
                <ExpressionSlot
                  key={mood}
                  mood={mood}
                  expression={expressions[mood] ?? null}
                  inVocabulary={(initial.moods ?? []).includes(mood)}
                  busy={busy}
                  onUpload={(file) => void uploadExpression(mood, file)}
                  onRemove={() => void removeExpression(mood)}
                />
              ))}
            </div>
          </div>
        );
      })()}

      <div className="flex items-center gap-3">
        <OutlineButton variant="accent" controlSize="md" onClick={saveFields} disabled={busy || !dirty}>
          {busy ? "Saving…" : "Save"}
        </OutlineButton>
        {saved ? (
          <span className="text-ui-xs text-[color:var(--status-success-text)]">Saved.</span>
        ) : null}
      </div>

      {error ? (
        <StatusMessage role="alert" tone="error">
          {error}
        </StatusMessage>
      ) : null}
    </div>
  );
}

/** One mood's slot in the expression grid: preview (or an empty dashed slot),
 * click/drop to upload, a corner ✕ to remove. Mirrors the avatar control. */
function ExpressionSlot({
  mood,
  expression,
  inVocabulary,
  busy,
  onUpload,
  onRemove,
}: {
  mood: string;
  expression: AdminExpression | null;
  inVocabulary: boolean;
  busy: boolean;
  onUpload: (file: File) => void;
  onRemove: () => void;
}) {
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const src = expression
    ? `/api/admin/identity/expressions/${encodeURIComponent(mood)}?v=${encodeURIComponent(expression.version)}`
    : null;

  return (
    <div className="flex flex-col items-center gap-1.5">
      <div
        role="button"
        tabIndex={0}
        aria-label={expression ? `Replace ${mood} portrait` : `Upload ${mood} portrait`}
        aria-disabled={busy}
        onClick={() => {
          if (!busy) fileInput.current?.click();
        }}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !busy) {
            e.preventDefault();
            fileInput.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files?.[0];
          if (file && !busy) onUpload(file);
        }}
        className={`group relative grid aspect-[3/4] w-24 cursor-pointer place-items-center overflow-hidden rounded-lg border-2 bg-[color:var(--ui-bg-soft)] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-[color:var(--ui-border-active)] ${
          dragging
            ? "border-dashed border-[color:var(--ui-border-active)]"
            : expression
              ? "border-ui"
              : "border-dashed border-ui"
        } ${busy ? "cursor-not-allowed opacity-70" : ""}`}
      >
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element -- BFF-served, dynamic; no loader needed
          <img src={src} alt={`${mood} portrait`} className="h-full w-full object-cover" />
        ) : (
          <span aria-hidden className="text-ui-2xs text-[color:var(--ui-ink-subtle)]">
            {dragging ? "Drop" : "Upload"}
          </span>
        )}
        <div
          className={`pointer-events-none absolute inset-0 flex items-center justify-center bg-black/55 text-white transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 ${
            dragging && expression ? "opacity-100" : "opacity-0"
          }`}
        >
          <span className="text-ui-2xs font-medium">
            {expression ? "Replace" : "Upload"}
          </span>
        </div>
        {expression && !busy ? (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onRemove();
            }}
            title={`Remove ${mood} portrait`}
            aria-label={`Remove ${mood} portrait`}
            className="absolute right-1 top-1 z-10 flex h-5 w-5 items-center justify-center rounded-full border border-ui bg-[color:var(--ui-bg)] text-ui-2xs text-[color:var(--ui-ink-subtle)] opacity-0 transition-opacity hover:text-[color:var(--ui-ink-danger)] group-hover:opacity-100 group-focus-within:opacity-100"
          >
            ✕
          </button>
        ) : null}
      </div>
      <span
        className={`font-mono text-ui-2xs ${
          inVocabulary
            ? "text-[color:var(--ui-ink-subtle)]"
            : "text-[color:var(--ui-ink-muted)] line-through"
        }`}
        title={inVocabulary ? undefined : "No longer in the mood vocabulary"}
      >
        {mood}
      </span>
      <input
        ref={fileInput}
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) onUpload(file);
          e.target.value = "";
        }}
      />
    </div>
  );
}
