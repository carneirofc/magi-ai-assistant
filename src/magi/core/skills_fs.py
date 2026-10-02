"""File-based skills — `SKILL.md` directories (the agentskills.io format).

A skill is a directory named after the skill, holding a `SKILL.md`: YAML
frontmatter (`name`, `description`, optional `license`, `compatibility`,
`metadata`, `allowed-tools`) followed by markdown instructions, plus any
bundled files (scripts, references, templates) beside it:

    ~/.magi/skills/
      release-notes/
        SKILL.md
        template.md

Skills use progressive disclosure: the lead's prompt carries only each skill's
`name: description`; the body (and bundled files) load on demand through the
`skill_view` tool. This module is pure file IO — parsing, discovery, safe
reads, and atomic writes. A malformed skill is skipped with a warning; the bot
always boots.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from magi.core.config import config
from magi.core.config_file import magi_home
from magi.core.log import log_warning
from magi.core.memory.adapters import atomic_write_text, emit_write

SKILL_FILE = "SKILL.md"
# Lowercase slug; doubles as the directory name and the skill's prompt path.
SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_\-]{1,40}$")
_MAX_VIEW_CHARS = 20_000
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


class SkillFileError(ValueError):
    """A SKILL.md that is missing, malformed, or fails validation."""


class SkillFrontmatter(BaseModel):
    """The agentskills.io frontmatter fields (unknown keys are ignored)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: str = Field(pattern=SKILL_NAME_RE.pattern)
    description: str = Field(min_length=1, max_length=1024)
    license: str | None = None
    compatibility: str | None = Field(default=None, max_length=500)
    metadata: dict[str, str] | None = None
    allowed_tools: list[str] | None = Field(default=None, alias="allowed-tools")

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def _split_tools(cls, value: object) -> object:
        # The spec writes it as a space-delimited string; accept a list too.
        return value.split() if isinstance(value, str) else value


@dataclass(frozen=True)
class FileSkill:
    meta: SkillFrontmatter
    body: str
    root: Path  # the skill's directory

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def description(self) -> str:
        return self.meta.description

    def bundled_files(self) -> list[str]:
        """Relative paths of everything shipped beside SKILL.md."""
        return sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if p.is_file() and p.name != SKILL_FILE and not p.name.startswith(".")
        )

    def read_file(self, relative: str) -> str:
        """One bundled file's text, confined to the skill directory."""
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root.resolve()) or not target.is_file():
            raise SkillFileError(f"no file {relative!r} in skill {self.name!r}")
        return target.read_text(encoding="utf-8", errors="replace")[:_MAX_VIEW_CHARS]


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """`(frontmatter block incl. fences, rest)` verbatim, or None when absent."""
    match = _FRONTMATTER_RE.match(text)
    return (text[: match.end()], text[match.end() :]) if match else None


def parse_skill_md(text: str, *, source: str) -> tuple[SkillFrontmatter, str]:
    """Split and validate a SKILL.md into (frontmatter, body)."""
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise SkillFileError(f"{source}: SKILL.md must start with a --- frontmatter block")
    try:
        raw: object = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillFileError(f"{source}: bad frontmatter YAML: {exc}") from exc
    try:
        meta = SkillFrontmatter.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise SkillFileError(f"{source}: invalid frontmatter — {problems}") from exc
    return meta, text[match.end() :].strip()


def load_skill(directory: Path) -> FileSkill:
    path = directory / SKILL_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillFileError(f"{path}: {exc}") from exc
    meta, body = parse_skill_md(text, source=str(path))
    if meta.name != directory.name:
        raise SkillFileError(f"{path}: name {meta.name!r} must match its directory")
    return FileSkill(meta=meta, body=body, root=directory)


def skill_write_root() -> Path:
    """Where new skills are written (and approved skill proposals land)."""
    return magi_home() / "skills"


def skill_dirs() -> list[Path]:
    """Search order: configured dirs first, then `$MAGI_HOME/skills`."""
    dirs = [Path(d).expanduser() for d in config.skills.dirs]
    home = skill_write_root()
    return dirs if home in dirs else [*dirs, home]


def discover(dirs: list[Path] | None = None) -> list[FileSkill]:
    """Every valid skill under `dirs` (first wins on a duplicate name)."""
    found: dict[str, FileSkill] = {}
    for base in dirs if dirs is not None else skill_dirs():
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not (child / SKILL_FILE).is_file():
                continue
            try:
                skill = load_skill(child)
            except SkillFileError as exc:
                log_warning(f"skills: skipped — {exc}")
                continue
            found.setdefault(skill.name, skill)
    return list(found.values())


def find_skill_dir(name: str, dirs: list[Path] | None = None) -> Path | None:
    """The directory holding skill `name` — the first on the search path, the
    same one `discover` would pick — or None."""
    for base in dirs if dirs is not None else skill_dirs():
        candidate = base / name
        if (candidate / SKILL_FILE).is_file():
            return candidate
    return None


def render_skill_md(meta: SkillFrontmatter, body: str) -> str:
    front = meta.model_dump(by_alias=True, exclude_none=True)
    return (
        f"---\n{yaml.safe_dump(front, sort_keys=False, allow_unicode=True)}---\n\n{body.strip()}\n"
    )


def write_skill(root: Path, text: str) -> Path:
    """Validate a full SKILL.md and write it atomically to `<root>/<name>/SKILL.md`."""
    meta, _body = parse_skill_md(text, source="new skill")
    path = root / meta.name / SKILL_FILE
    atomic_write_text(path, text if text.endswith("\n") else text + "\n")
    emit_write(path)
    return path
