# Architecture

## Main goal

magi is the **reusable core of a personal AI assistant**. It is not a single bot;
it is the engine that several bots share:

- **One shared brain, many channels.** A Discord bot, an HTTP service, and an
  OpenAI-compatible shim all drive the *same* assembled stack. Only the transport
  differs — the conversation logic, memory, team, and tools are wired once.
- **Deliberate memory, not framework magic.** The model's long-term knowledge of
  a user lives in durable, inspectable files that are written *on purpose* (by a
  post-turn curator), never silently auto-extracted. You can open them and read
  what the assistant remembers.
- **Model-agnostic.** A local `llama.cpp` `llama-server` is the default chat
  backend; Claude (via a LiteLLM proxy) and Ollama are drop-in alternatives — the
  team code never names a provider.
- **Engine + persona.** The public engine boots and chats with a neutral demo
  persona. A private persona repo (e.g. `alyssa`) overlays prompts and registers
  its own specialists *without editing the engine tree*. See
  [split-plan.md](split-plan.md).

## Design principles

| Principle | How it shows up in the code |
|---|---|
| **`core/` is model-free** | [`magi/core`](../src/magi/core) never imports a model. Anything needing an LLM (summarizers, the curator) is injected as a callable by the agent layer. |
| **Dependency injection, no globals** | The team, memory manager, and DB are constructed at composition roots and passed in. Scope flows through a `ContextVar`, never as a tool argument. |
| **Code-first config** | All settings are plain Python set at the entrypoint via `configure(...)`. Only *secrets* come from `.env`. See [configuration.md](configuration.md). |
| **Graceful degradation** | Optional features (storage, knowledge, semantic search, MCP) degrade to "tool not attached" when their backend is missing or down. The bot always boots. |
| **Pluggable extension points** | Members are a registry (`register_member`), prompts are an overlay (`load_prompt`), tools are a list. A persona extends all three from the outside. |

## Layered package map

```mermaid
flowchart TD
    subgraph entry["Entrypoints (deployment + secrets)"]
        M1[main.py — Discord]
        M2[main_api.py — HTTP]
    end

    subgraph ch["magi.channels (transport)"]
        BOOT[bootstrap.py<br/>shared wiring]
        DISC[discord.py]
        API[api.py]
    end

    subgraph ag["magi.agent (model-bound brain)"]
        TEAM[team.py]
        MEMB[members/]
        MODEL[model.py]
        CUR[curator.py]
        SUM[summarizer.py]
        TOOLS[tools/]
    end

    subgraph co["magi.core (model-free mechanism)"]
        CONV[conversation.py]
        CFG[config.py]
        MEMMOD[memory/]
        KN[knowledge/]
        ST[storage/]
        MEDIA[media.py]
        DBMOD[db.py]
        PR[prompts.py]
    end

    M1 --> DISC --> BOOT
    M2 --> API --> BOOT
    BOOT --> TEAM
    BOOT --> CONV
    BOOT --> MEMMOD
    TEAM --> MEMB & MODEL & TOOLS
    TEAM --> CUR & SUM
    CONV --> MEMMOD & MEDIA
    TEAM --> KN & ST
    MEMMOD --> DBMOD
    CFG -. read by all .-> co
```

Dependency direction is strictly **downward**: channels depend on agent + core;
agent depends on core; core depends on nothing above it. `core` stays
model-free — the curator and summarizers live in `agent/` precisely because they
need a model, and they are handed to `core` as injected callables.

## Composition: how the stack is built

Every channel calls one shared assembler,
[`build_conversation_service`](../src/magi/channels/bootstrap.py), in a fixed order:

```
summarizers (gated by config) → memory → team(memory, members) →
ConversationService(team, memory, channel_guidance)
```

```mermaid
flowchart LR
    CFG[config] --> BOOT[build_conversation_service]
    BOOT -->|if session_summary| SUM[session summarizer]
    BOOT -->|if memory_curation| CUR[memory curator]
    SUM --> MEM[MemoryManager]
    CUR --> MEM
    BOOT --> MEM
    MEM --> TEAM[build_team]
    TEAM --> CS[ConversationService]
    CS --> CH[channel transport]
```

Each channel then adds only its transport-specific pieces: Discord adds a
`discord.Client` and the Discord output-guidance prompt; the API adds the FastAPI
app, auth, CORS, and any MCP lifespan.

## The request lifecycle

[`ConversationService`](../src/magi/core/conversation.py) owns the run + memory
flow for one inbound message, free of any channel concern. Both the whole-reply
(`handle`) and streaming (`handle_stream`) paths share the same head (prepare
input) and tail (finish turn).

```mermaid
sequenceDiagram
    participant Ch as Channel
    participant CS as ConversationService
    participant Mem as MemoryManager
    participant Team as Agent Team (lead+members)
    participant Cur as Curator (off-path)

    Ch->>CS: handle(user_id, session_id, text, media)
    CS->>Mem: set_scope(user, session)
    CS->>Mem: build_context(query=text)
    Mem-->>CS: assembled memory block
    CS->>Mem: record_user_turn(text)
    Note over CS: input = context block + user text
    CS->>Team: arun(input, user_id, session_id, **media)
    Team->>Team: lead routes → members → merges reply
    Team-->>CS: RunOutput (text, reasoning, media)
    CS->>Mem: record_assistant_turn(reply)
    CS->>Mem: maybe_summarize_session()
    CS->>Cur: maybe_curate(user_text, reply)
    Cur-->>Mem: ADD/UPDATE/DELETE facts, episode, persona
    CS-->>Ch: ConversationReply(text, media, …)
```

Key invariants:

- **Context rides inside the run input**, never on the shared runner. Mutating
  `runner.additional_context` would race concurrent conversations — one user could
  see another's memory.
- **Scope is set once per message** (`set_scope`) and read by tools via a
  `ContextVar`. The `MemoryManager` is a single shared instance, so `mem` is
  resolved per-access, never cached (see [ADR 0001](adr/0001-per-kind-memory-modules.md)).
- **Memory is folded/curated once per turn**, from the final text — identical for
  streaming and non-streaming.
- **Failures never hand the channel silence.** A run that errors returns an honest
  error reply; a run that completes empty returns a fallback. Curation failures are
  swallowed — they must never break a chat.

## The agent team

A [`Team`](../src/magi/agent/team.py) is a lead model that reads each member's
`role`, routes the message to the right specialist (or coordinates several), then
merges their work into one reply in its own voice.

```mermaid
flowchart TD
    U[User message + injected context] --> LEAD{Lead model<br/>router brain}
    LEAD -->|handles most itself| OUT[Single merged reply]
    LEAD -->|delegates when needed| ASST[Assistant]
    LEAD -->|delegates when needed| RES[Researcher]
    LEAD -->|Discord channel only| DISCM[Discord Bot]
    LEAD -.persona overlay.-> EXTRA[Custom specialists<br/>register_member]
    ASST --> OUT
    RES --> OUT
    DISCM --> OUT

    LEAD --- LT[Lead-only tools:<br/>vision · media · http · memory-read ·<br/>storage · knowledge · thinking · introspection]
```

- The **lead** is multimodal and tool-equipped; it answers most requests directly
  and delegates only when a task needs separate expertise.
- **Members** are built from `MEMBER_BUILDERS` (assistant, researcher, Discord
  helper by default). A persona appends its own with `register_member(builder)`.
- agno's automatic history-stuffing and memory extraction are **turned off**
  (`add_history_to_context=False`, `update_memory_on_run=False`) — magi injects its
  own memory deliberately.
- Robustness: a `tool_call_limit` bounds runaway delegation loops; a tool hook logs
  every member/tool call and converts a raising tool into a lead-visible error
  instead of aborting the run.

See [agent-and-tools.md](agent-and-tools.md) for the full member and tool roster.

## Model backends

[`magi/agent/model.py`](../src/magi/agent/model.py) is the single place that turns
a declarative `ModelDefinition` (id, capabilities, context window) into a concrete
agno `Model`, dispatching per provider.

```mermaid
flowchart LR
    DEF[ModelDefinition<br/>from config] --> BM[build_model]
    BM -->|model_provider=litellm| LL[LiteLLM proxy<br/>→ Claude / Databricks / …]
    BM -->|model_provider=llamacpp| LC[llama-server /v1<br/>local default]
    BM -->|model_provider=ollama| OL[Ollama<br/>dormant fallback]
```

- **`llamacpp`** (default deployment) talks directly to a `llama-server`
  OpenAI-compatible `/v1`. The context window is fixed at launch (`--ctx-size`);
  `lead_num_ctx`/`member_num_ctx` are budgets for context assembly only and must
  match it. Sampling overrides ride per-request via `extra_body`.
- **`litellm`** routes through a LiteLLM proxy (a `litellm_proxy/` prefix tells the
  SDK to use `api_base`). A small `ProxyLiteLLM` subclass normalizes no-argument
  tool calls some proxied backends emit.
- **`ollama`** is a dormant local fallback.

Add a provider by extending `ModelProviderEnum`, writing a `_build_<provider>`,
and registering it in `_BUILDERS`.

## Where to go next

- The memory subsystem — the project's centerpiece — has its own deep dive:
  [memory.md](memory.md).
- Channel contracts (endpoints, SSE, media, the OpenAI shim): [channels.md](channels.md).
- Every configuration field: [configuration.md](configuration.md).
