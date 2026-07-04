"""Git-backed memory — the memory root as a version-controlled repository.

The filesystem memory store (magi/core/memory/store) is deliberate and inspectable:
plain markdown + JSON files an operator can open and read. This backend adds *time*
to that — it makes the memory root (`config.memory_dir`) a real git repository and
commits **every** deliberate write, so the whole tree carries a full, revertible
history any git tool (log, blame, diff, revert) can read. Nothing here is
model-facing; it's pure plumbing under the store.

How it hooks in without touching the dumb IO layer: the adapters and the identity
store announce each mutation through `adapters.emit_write(path)` (see
`adapters.set_write_observer`). `MemoryGit.install()` registers `on_write` as that
observer; from then on each write is staged and committed. One repo per memory root,
one observer per process — a `threading.Lock` serializes the stage+commit so the
single shared `MemoryManager`'s concurrent sessions can't race the git index.

GitPython is imported lazily (only inside the methods) so the base install need not
carry it — it's the optional `git` extra. `build_memory_git` is the single entry
point: it honors `config.memory_git_enabled`, and returns `None` (with a warning,
never raising) when the flag is off, the extra is missing, or the repo can't be
initialized — so a deployment without git still boots and its memory writes simply
stay plain files.

Failure is always non-fatal: a git error during a write is swallowed with a warning,
because a memory write (and the chat it rides on) must never break over version
control.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from agno.utils.log import log_info, log_warning

from magi.core.config import config
from magi.core.memory import adapters

if TYPE_CHECKING:  # pragma: no cover - typing only
    from git import Repo

# The single active backend, for an explicit owner + inspection/teardown (tests).
# The process-global write observer also holds a reference (the bound `on_write`),
# so the backend stays alive for the life of the process once installed.
_active: Optional["MemoryGit"] = None


class MemoryGit:
    """Initializes and manages the git repo at the memory root, committing per write."""

    def __init__(self, root: Path, *, author_name: str, author_email: str):
        self._root = Path(root).resolve()
        self._author_name = author_name
        self._author_email = author_email
        # Serializes stage+commit: the memory manager is shared across concurrent
        # sessions, and the git index / HEAD are single shared files.
        self._lock = threading.Lock()
        self._repo: Optional["Repo"] = None

    # --- repo lifecycle -----------------------------------------------------
    def _open(self) -> "Repo":
        """The cached `Repo`, opened on first use (assumes `ensure_repo` ran)."""
        if self._repo is None:
            import git  # noqa: PLC0415 — optional dependency, imported on demand.

            self._repo = git.Repo(self._root)
        return self._repo

    def ensure_repo(self) -> None:
        """Create the repo if absent, pin the commit identity, and baseline-snapshot
        any pre-existing content. Idempotent. Raises only on a hard git failure or a
        nested-repo refusal (the factory catches it and disables the backend)."""
        import git  # noqa: PLC0415 — optional dependency, imported on demand.

        self._root.mkdir(parents=True, exist_ok=True)
        if (self._root / ".git").exists():
            repo = git.Repo(self._root)
        else:
            # Refuse to create a repo *inside* another one (typically the source tree
            # when memory_dir is left at its in-repo default): a nested repo would bury
            # the memory history inside the code history and stage the whole tree. The
            # memory root must be its own top-level repo, outside the source checkout.
            enclosing = self._enclosing_repo()
            if enclosing is not None:
                raise RuntimeError(
                    f"refusing to initialize a nested git repo: memory root {self._root} "
                    f"is inside the repository at {enclosing}. Point memory_dir at a "
                    "directory outside the source tree (see main.py / config.memory_dir)."
                )
            repo = git.Repo.init(self._root)
            log_info(f"memory: initialized git repo at {self._root}")
        self._repo = repo
        self._pin_identity(repo)
        # A memory tree that existed before git was enabled: capture it as one
        # baseline commit rather than folding it into the first later write.
        repo.git.add("-A")
        if self._staged(repo):
            self._commit(repo, "memory: baseline snapshot")

    def _enclosing_repo(self) -> Optional[Path]:
        """The nearest ancestor directory that is itself a git repo, or None.

        Used to reject initializing the memory repo inside another one. Only ancestors
        are checked — the root carrying its own `.git` is our repo, handled separately.
        A `.git` file (not dir) counts too: that's how git worktrees and submodules
        mark a work tree, and nesting under those is just as wrong.
        """
        for parent in self._root.parents:
            if (parent / ".git").exists():
                return parent
        return None

    def _pin_identity(self, repo: "Repo") -> None:
        """Write the commit identity into the repo config so commits never depend on
        the host's global git config, and never GPG-sign these automated commits."""
        writer = repo.config_writer()
        try:
            writer.set_value("user", "name", self._author_name)
            writer.set_value("user", "email", self._author_email)
            writer.set_value("commit", "gpgsign", "false")
        finally:
            writer.release()

    # --- observer install ---------------------------------------------------
    def install(self) -> None:
        """Register `on_write` as the memory write observer (this process)."""
        global _active
        adapters.set_write_observer(self.on_write)
        _active = self

    def uninstall(self) -> None:
        """Detach the observer (mainly for tests — production installs once)."""
        global _active
        if _active is self:
            adapters.set_write_observer(None)
            _active = None

    # --- the per-write commit ----------------------------------------------
    def on_write(self, path: Path) -> None:
        """Stage and commit a single memory-file write. Never raises — a git failure
        must not break the memory write that triggered it."""
        p = Path(path)
        try:
            rel = p.resolve().relative_to(self._root)
        except (ValueError, OSError):
            # A write outside our repo root (defensive — the observer is process-wide).
            return
        with self._lock:
            try:
                repo = self._open()
                # `-A -- <path>` stages an add, a modification, OR a deletion of just
                # this path, so one commit maps to one logical write.
                repo.git.add("-A", "--", rel.as_posix())
                if self._staged(repo):
                    self._commit(repo, f"memory: update {rel.as_posix()}")
            except Exception as exc:  # noqa: BLE001 — git must never break a memory write.
                log_warning(
                    f"memory: git commit failed for {rel.as_posix()}: "
                    f"{type(exc).__name__}: {exc}"
                )

    def _staged(self, repo: "Repo") -> bool:
        """Whether the index differs from HEAD (works before the first commit, where
        HEAD is unborn and the diff is against the empty tree)."""
        from git.exc import GitCommandError  # noqa: PLC0415 — optional dependency.

        try:
            # `--quiet` implies `--exit-code`: status 1 means there are staged changes.
            repo.git.diff("--cached", "--quiet")
            return False
        except GitCommandError as exc:
            if exc.status == 1:
                return True
            raise

    def _commit(self, repo: "Repo", message: str) -> None:
        """Commit the staged index. Identity comes from the repo config pinned in
        `_pin_identity`, so no author/committer env is required."""
        repo.git.commit("-m", message)


def build_memory_git(
    root: Path,
    *,
    enabled: Optional[bool] = None,
    author_name: Optional[str] = None,
    author_email: Optional[str] = None,
) -> Optional[MemoryGit]:
    """Build, initialize, and install the git-backed memory backend for `root`, or
    return `None` when it's disabled or unbuildable.

    The single entry point. `enabled`/`author_name`/`author_email` override the
    matching `config.memory_git_*` defaults when given (so operator overlays from
    `magi/core/settings` can steer it); each falls back to config when `None`. Returns
    `None` (with a warning) rather than raising when it's off, GitPython is absent, or
    the repo can't be initialized — so a deployment without git still boots and its
    memory writes stay plain files. On success the write observer is installed, so
    every subsequent memory write is committed.
    """
    enabled = config.memory_git_enabled if enabled is None else enabled
    if not enabled:
        return None
    try:
        import git  # noqa: F401, PLC0415 — presence probe; real repo opened lazily.
    except ImportError:
        log_warning(
            "memory: memory_git_enabled but GitPython is not installed — memory "
            "versioning disabled. Install the optional extra (`uv sync --extra git`)."
        )
        return None
    backend = MemoryGit(
        root,
        author_name=author_name or config.memory_git_author_name,
        author_email=author_email or config.memory_git_author_email,
    )
    try:
        backend.ensure_repo()
    except Exception as exc:  # noqa: BLE001 — a broken repo must not stop the app booting.
        log_warning(
            f"memory: could not initialize git repo at {Path(root).resolve()}: "
            f"{type(exc).__name__}: {exc} — memory versioning disabled"
        )
        return None
    backend.install()
    log_info(
        f"memory: git-backed memory active at {backend._root} "
        f"(commits as {backend._author_name} <{backend._author_email}>)"
    )
    return backend
