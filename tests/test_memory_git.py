"""Tests for git-backed memory (core/memory/git_backend).

When `memory_git_enabled` is on, the memory root becomes a git repository and every
deliberate write is committed. These pin that behavior: a repo is created, each write
lands one commit, the working tree stays clean, deletions are versioned, the commit
identity is the pinned one (not the host's), and the whole thing is a no-op when the
flag is off. GitPython (the optional `git` extra) is required, so the module skips
cleanly where it isn't installed.
"""

import subprocess

import pytest

from magi.core.config import config, configure
from magi.core.memory import adapters
from magi.core.memory.git_backend import build_memory_git
from magi.core.memory.store import FileMemoryStore

pytest.importorskip("git", reason="git-backed memory needs the optional `git` extra")


@pytest.fixture
def git_memory(tmp_path):
    """A git-enabled memory root, with the global config + write observer restored
    afterward so the process-wide observer never leaks into other tests."""
    prior_enabled = config.memory_git_enabled
    configure(
        memory_git_enabled=True,
        memory_dir=str(tmp_path),
        memory_git_author_name="magi-memory",
        memory_git_author_email="magi-memory@localhost",
    )
    backend = build_memory_git(tmp_path)
    assert backend is not None
    try:
        yield tmp_path, backend
    finally:
        backend.uninstall()
        configure(memory_git_enabled=prior_enabled)


def _git(root, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _log(root) -> list[str]:
    # `git log` exits 128 on an unborn HEAD (no commits yet); treat that as empty.
    result = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%s"], capture_output=True, text=True
    )
    return result.stdout.splitlines() if result.returncode == 0 else []


def test_repo_is_initialized_and_starts_clean(git_memory):
    root, _ = git_memory

    assert (root / ".git").is_dir()
    assert _git(root, "status", "--porcelain") == ""


def test_each_write_lands_one_commit(git_memory):
    root, _ = git_memory
    scoped = FileMemoryStore(root).scoped("u1", "s1")

    before = len(_log(root))
    scoped.long_term.append("likes tea")
    scoped.long_term_facts.add("prefers dark mode")
    after = len(_log(root))

    # Two writes → two new commits, and nothing left uncommitted.
    assert after == before + 2
    assert _git(root, "status", "--porcelain") == ""
    assert _log(root)[0] == "memory: update users/u1/long_term_facts.json"


def test_unchanged_write_makes_no_empty_commit(git_memory):
    root, backend = git_memory
    note = root / "note.txt"
    note.write_text("stable", encoding="utf-8")

    backend.on_write(note)  # first sighting → committed
    count = len(_log(root))
    backend.on_write(note)  # file unchanged on disk → nothing staged, no commit

    assert len(_log(root)) == count


def test_deletion_is_versioned(git_memory):
    root, _ = git_memory
    facts = FileMemoryStore(root).scoped("u1", "s1").long_term_facts

    facts.add("temporary")
    facts.delete()

    assert not facts.path.exists()
    assert _git(root, "status", "--porcelain") == ""
    # The deletion is a commit of its own; the file is gone from the working tree.
    assert _git(root, "ls-files", "users/u1/long_term_facts.json") == ""


def test_commit_identity_is_pinned_not_host_global(git_memory):
    root, _ = git_memory
    FileMemoryStore(root).scoped("u1", "s1").long_term.append("hi")

    assert _git(root, "log", "-1", "--format=%an <%ae>") == "magi-memory <magi-memory@localhost>"


def test_preexisting_content_is_baseline_snapshotted(tmp_path):
    # Content written before git is enabled should land as one baseline commit, not
    # get folded into the first later write.
    (tmp_path / "persona.md").write_text("# Persona\n", encoding="utf-8")
    prior_enabled = config.memory_git_enabled
    configure(memory_git_enabled=True, memory_dir=str(tmp_path))
    backend = build_memory_git(tmp_path)
    assert backend is not None
    try:
        assert _log(tmp_path) == ["memory: baseline snapshot"]
        assert _git(tmp_path, "ls-files") == "persona.md"
    finally:
        backend.uninstall()
        configure(memory_git_enabled=prior_enabled)


def test_refuses_to_nest_inside_an_existing_repo(tmp_path):
    """A memory root inside another git repo (e.g. the source tree) must NOT get its
    own nested repo — the backend disables itself instead of burying memory history
    in the enclosing repo."""
    outer = tmp_path / "source_tree"
    outer.mkdir()
    subprocess.run(["git", "-C", str(outer), "init", "-q"], check=True)
    nested = outer / "data" / "memory"  # the in-repo default shape

    prior_enabled = config.memory_git_enabled
    configure(memory_git_enabled=True, memory_dir=str(nested))
    try:
        assert build_memory_git(nested) is None  # refused, not initialized
        assert not (nested / ".git").exists()  # no nested repo created
    finally:
        adapters.set_write_observer(None)
        configure(memory_git_enabled=prior_enabled)


def test_disabled_is_a_no_op(tmp_path):
    prior_enabled = config.memory_git_enabled
    configure(memory_git_enabled=False, memory_dir=str(tmp_path))
    try:
        assert build_memory_git(tmp_path) is None
        FileMemoryStore(tmp_path).scoped("u1", "s1").long_term.append("no repo here")
        assert not (tmp_path / ".git").exists()
    finally:
        adapters.set_write_observer(None)
        configure(memory_git_enabled=prior_enabled)
