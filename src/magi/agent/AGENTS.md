# Purpose

The **model-bound brain**: a `Team` (lead model that routes to specialist members
and merges their work into one reply), the per-provider model builders, the
post-turn curator + summarizers, and the tool registry. This is the only layer
allowed to name/construct a model.

# Local Contracts

- **The team never names a provider.** `model.py` is the single place that turns a
  declarative `ModelDefinition` into a concrete agno `Model`, dispatching on
  `model_provider` (`llamacpp` default, `litellm`, `ollama`). Add a provider by
  extending `ModelProviderEnum`, writing `_build_<provider>`, and registering it in
  `_BUILDERS` — nothing else should branch on provider.
- **agno's auto-memory is off.** `add_history_to_context=False`,
  `update_memory_on_run=False` — magi injects its own memory deliberately. Do not
  re-enable them.
- **Curator/summarizers are injected into `core`, not called from it.** They live
  here because they need a model; `core` receives them as callables
  (`curator.py` builds the `CurateFn`). Curation runs off the reply path (a
  background tail scheduled by `ConversationService`) and its failures are
  swallowed. The curator's evolution store is injected by the composition root
  (`channels/bootstrap.py`) — never rebuilt from raw `config.memory_dir` — and
  persona rules follow `config.persona_learning` (`route_persona_adjustment`).
- **Members and tools are registries extended from the persona:**
  - Members: `MEMBER_BUILDERS` + `register_member(builder)` (`members/`).
  - Tools: add an engine tool as a `@tool` function under `tools/`, import it into
    `tools/__init__.py`, and append to `DEFAULT_TOOLS`. A persona instead calls
    `register_tool(fn)` (member set) or `register_lead_toolkit(builder)` (lead-level,
    memory-injected) at its entrypoint. `enabled_tools()` is the single resolution
    point — never duplicate the default set in a builder. Registration is idempotent.
  - Deliberate-memory tools are **not** in `DEFAULT_TOOLS`: they bind to the injected
    `MemoryManager` (`tools/memory.py`) and attach to the lead in `team.py`.
    `recall_conversation` attaches only when `search_sessions` is off — never
    offer the lead two tools for the same lookup.
- **Skills (`skills.py`)** come from two sources: Python `register_skill` (prompt
  fragment + tools + gate; prompt inlined, evolution-proposable) and SKILL.md
  files (`core/skills_fs.py`; only `name: description` reaches the prompt, the
  body loads via `skill_view`). `all_skills()` / `active_skills()` merge them —
  a Python skill wins a name collision; `config.skills.disabled` filters both.
  Agent-written skills (`tools/skills.py`) follow `skills.agent_write`
  (`propose` goes through the evolution queue, never straight to disk). The
  skill tools see `enabled_file_skills()` only, a patch is written back into
  the skill's own directory (never the write root, where it would be
  shadowed), and a name in `registered_skill_names()` can't be created.
- **Toolsets (`toolsets.py`)**: every lead tool group in `team.py` is wrapped in
  `toolset("<name>", …)`; a new group needs a `TOOLSETS` entry (a test enforces
  it).
- A tool hook logs every member/tool call and converts a raising tool into a
  lead-visible error instead of aborting the run; `tool_call_limit` bounds runaway
  delegation.

# Work Guidance

Roster reference: [../../../docs/agent-and-tools.md](../../../docs/agent-and-tools.md).
Optional tool backends (websearch, seanime MCP, media/vision) lazy-import their dep
and no-op when missing — keep that contract.

# Verification

`uv run pytest -q` (`tests/test_team.py`, `test_model.py`, `test_curator.py`,
`test_skills.py`, `test_skills_fs.py`, `test_tool_registry.py`, and the per-tool
suites).
