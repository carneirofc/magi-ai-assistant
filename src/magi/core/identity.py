"""Global bot identity — the personalization the model presents as itself.

A single, non-scoped profile that lives beside the persona on the memory root:
a display name, a free-form description, and a profile picture. Unlike the
persona (evolving *behavioral* notes) this is the bot's *presented identity* —
what it is called and how it looks — set by an operator, injected into every run
so the model both **knows** it (name + description as text) and **sees** it (the
avatar as an image), and shown in the frontend as the assistant's face.

On-disk layout (under the memory root, next to `persona.md`):

    identity.json                # {display_name, description, avatar: {...}}
    identity/avatar.<ext>        # the raw profile-picture bytes

Pure IO, model-free: it reads/writes the files, renders the context text, and
hands back the avatar bytes. `ConversationService` injects it into a run; the
admin API edits it; the chat API serves the picture to the UI.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _emit_write(path: Path) -> None:
    """Announce an identity-file mutation to the memory write observer (see
    magi/core/memory/adapters). Imported lazily inside the call to sidestep an import
    cycle: `magi.core.memory` imports the store, which imports this module."""
    from magi.core.memory.adapters import emit_write  # noqa: PLC0415 — avoids a cycle.

    emit_write(path)


# The image mime types an avatar may be stored as, mapped to the on-disk
# extension. A raster format the vision model can read; the set is deliberately
# small (an operator uploads a normal picture, not an arbitrary blob).
_MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/avif": "avif",
}


@dataclass(frozen=True)
class BotIdentity:
    """The bot's presented identity, as read off disk (the picture stays on disk)."""

    display_name: str = ""
    description: str = ""
    # The stored picture's mime type + original upload filename (both None when no
    # picture is set). The bytes are read separately via `IdentityStore.avatar_bytes`.
    avatar_mime: Optional[str] = None
    avatar_filename: Optional[str] = None

    @property
    def has_avatar(self) -> bool:
        return bool(self.avatar_mime)

    @property
    def is_empty(self) -> bool:
        """True when nothing has been set — the injection/serialization no-op case."""
        return not (self.display_name or self.description or self.has_avatar)


class IdentityStore:
    """The global bot identity on disk: a JSON metadata sidecar + the avatar bytes.

    Everything is derived from `identity.json` (the source of truth for the
    fields and which avatar file is current); the picture rides as a sibling
    `identity/avatar.<ext>` so the raw bytes never bloat the JSON.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.meta_path = self.root / "identity.json"
        self.avatar_dir = self.root / "identity"

    # --- reads --------------------------------------------------------------
    def _read_json(self) -> dict:
        """The parsed metadata, or `{}` when absent/corrupt (never raises)."""
        if not self.meta_path.exists():
            return {}
        try:
            parsed = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def read(self) -> BotIdentity:
        data = self._read_json()
        avatar = data.get("avatar") if isinstance(data.get("avatar"), dict) else {}
        mime = avatar.get("mime")
        filename = avatar.get("filename")
        return BotIdentity(
            display_name=str(data.get("display_name", "") or ""),
            description=str(data.get("description", "") or ""),
            avatar_mime=str(mime) if mime else None,
            avatar_filename=str(filename) if filename else None,
        )

    def avatar_bytes(self) -> Optional[tuple[bytes, str]]:
        """The avatar's `(bytes, mime)`, or None when no picture is set / readable."""
        avatar = self._read_json().get("avatar")
        if not isinstance(avatar, dict):
            return None
        stored, mime = avatar.get("stored"), avatar.get("mime")
        if not stored or not mime:
            return None
        path = self.avatar_dir / str(stored)
        if not path.exists():
            return None
        try:
            return path.read_bytes(), str(mime)
        except OSError:
            return None

    def version(self) -> str:
        """Optimistic-concurrency token over the identity's full state.

        Hashes the metadata bytes plus the avatar bytes, so any edit — a renamed
        field or a swapped picture — moves the token (the admin editor rejects a
        stale write with a 409, matching the facts/raw-file endpoints)."""
        h = hashlib.sha256()
        h.update(self.meta_path.read_bytes() if self.meta_path.exists() else b"")
        avatar = self.avatar_bytes()
        if avatar is not None:
            h.update(avatar[0])
        return h.hexdigest()

    # --- writes -------------------------------------------------------------
    def set_fields(self, *, display_name: str, description: str) -> BotIdentity:
        """Set the name + description, leaving any picture untouched."""
        data = self._read_json()
        data["display_name"] = display_name.strip()
        data["description"] = description.strip()
        self._write_json(data)
        return self.read()

    def set_avatar(
        self, data: bytes, mime: str, filename: Optional[str] = None
    ) -> BotIdentity:
        """Replace the profile picture. Raises ValueError on an unsupported mime."""
        mime = (mime or "").strip().lower()
        ext = _MIME_EXT.get(mime)
        if ext is None:
            raise ValueError(f"unsupported image mime type {mime!r}")
        self.avatar_dir.mkdir(parents=True, exist_ok=True)
        # Drop any prior avatar first — the new one may have a different extension,
        # so overwriting by name would leave the old file orphaned and ambiguous.
        self._remove_avatar_files()
        stored = f"avatar.{ext}"
        (self.avatar_dir / stored).write_bytes(data)
        # Version the avatar subtree (the just-written bytes and the removed prior
        # file); `_write_json` below versions the metadata sidecar.
        _emit_write(self.avatar_dir)
        meta = self._read_json()
        meta["avatar"] = {
            "mime": mime,
            "stored": stored,
            "filename": (filename or "").strip() or None,
        }
        self._write_json(meta)
        return self.read()

    def clear_avatar(self) -> BotIdentity:
        """Remove the profile picture; the name + description stay."""
        self._remove_avatar_files()
        _emit_write(self.avatar_dir)
        meta = self._read_json()
        meta.pop("avatar", None)
        self._write_json(meta)
        return self.read()

    def _remove_avatar_files(self) -> None:
        if not self.avatar_dir.is_dir():
            return
        for path in self.avatar_dir.glob("avatar.*"):
            try:
                path.unlink()
            except OSError:
                pass

    def _write_json(self, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _emit_write(self.meta_path)

    # --- render (injected into every run) -----------------------------------
    def context_text(self) -> str:
        """The identity block prepended to a run's context, or '' when nothing set.

        States the bot's name and description and, when a picture is set, tells the
        model it *has* a profile picture standing for its appearance — that it can
        look at or share on request. The picture itself is NOT fed into context each
        turn (that reads as user-supplied content and derails the model); the model
        pulls it in only when relevant, via its profile-picture tools."""
        ident = self.read()
        if ident.is_empty:
            return ""
        lines = ["# Your identity (how you present yourself)"]
        if ident.display_name:
            lines.append(f"Your name is {ident.display_name}.")
        if ident.description:
            lines.append(ident.description)
        if ident.has_avatar:
            lines.append(
                "You have a profile picture that represents your appearance. You are not "
                "shown it every turn; if the user asks what you look like or to see it, you "
                "can look at it yourself or send it to them with your profile-picture tools."
            )
        return "\n".join(lines)
