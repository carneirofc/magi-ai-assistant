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
  (`curator.py` builds the `CurateFn`). Curation runs off the reply path and its
  failures are swallowed.
- **Members and tools are registries extended from the persona:**
  - Members: `MEMBER_BUILDERS` + `register_member(builder)` (`members/`).
  - Tools: add an engine tool as a `@tool` function under `tools/`, import it into
    `tools/__init__.py`, and append to `DEFAULT_TOOLS`. A persona instead calls
    `register_tool(fn)` (member set) or `register_lead_toolkit(builder)` (lead-level,
    memory-injected) at its entrypoint. `enabled_tools()` is the single resolution
    point — never duplicate the default set in a builder. Registration is idempotent.
  - Deliberate-memory tools are **not** in `DEFAULT_TOOLS`: they bind to the injected
    `MemoryManager` (`tools/memory.py`) and attach to the lead in `team.py`.
- **Skills (`skills.py`)** bundle a prompt fragment + tools + an `enabled` gate under
  one `register_skill`; skill prompts are evolution-proposable by default.
- A tool hook logs every member/tool call and converts a raising tool into a
  lead-visible error instead of aborting the run; `tool_call_limit` bounds runaway
  delegation.

# Work Guidance

Roster reference: [../../../docs/agent-and-tools.md](../../../docs/agent-and-tools.md).
Optional tool backends (websearch, seanime MCP, media/vision) lazy-import their dep
and no-op when missing — keep that contract.

# Verification

`uv run pytest -q` (`tests/test_team.py`, `test_model.py`, `test_curator.py`,
`test_skills.py`, `test_tool_registry.py`, and the per-tool suites).
