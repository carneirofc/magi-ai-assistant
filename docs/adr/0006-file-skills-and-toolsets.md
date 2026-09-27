# 0006 — File skills (SKILL.md) and toolsets

- Status: Accepted
- Date: 2026-09-27
- Builds on: `agent/skills.py` (Python skills), the evolution queue
  (`core/evolution.py`)

## Context

Skills were Python-only: a persona had to write code to teach the assistant a
procedure, and every active skill's prompt was inlined into every run. The
assistant had no way to keep what it learned from a hard task other than
memory facts, which are about the user, not about how to do things.

## Decision

1. **SKILL.md files** in the agentskills.io format (`core/skills_fs.py`):
   a directory per skill, YAML frontmatter validated by pydantic, a markdown
   body, optional bundled files. Found in `skills.dirs` then `$MAGI_HOME/skills`.
2. **Progressive disclosure**: file skills contribute one index line
   (`name: description`) to the lead's prompt; `skill_view` loads the body or
   a bundled file (path-confined to the skill directory).
3. **One registry view**: `all_skills()` merges Python and file skills; a
   Python skill wins a name collision; `skills.disabled` hides either.
4. **The assistant may write skills**, governed by `skills.agent_write`:
   `propose` (default) queues a `skill` proposal the operator approves in the
   existing admin queue; `direct` writes atomically; `off` is read-only.
5. **Toolsets**: lead tools are grouped by name in `team.py`
   (`agent/toolsets.py`); `toolsets.disabled` drops groups.

## Consequences

- Teaching a procedure is a text file, portable to other agentskills.io agents.
- Prompt size grows by one line per file skill, not by its body.
- Self-improvement stays human-gated by default: without `evolution_enabled`,
  `propose` degrades to read-only (doctor warns).
