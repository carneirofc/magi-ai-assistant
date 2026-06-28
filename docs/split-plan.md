# Open-source split & local-dev plan

_Status: planning. Created 2026-06-28._

How to split this project into a **public open-source engine** and a **private
`alyssa` persona repo**, while developing both locally without publishing a
version on every change.

---

## Decisions (locked)

| Question | Decision |
|---|---|
| What is the public repo? | **Runnable app + a neutral demo persona** — boots and chats out of the box; Alyssa overlays it privately. |
| How does `alyssa` consume the engine? | **Versioned dependency** — but a local *editable path source* during active dev (see below); a published version only when shipping. |
| Where does the memory/knowledge system live? | **Split: mechanism public, policy private** — store/index/curation plumbing is open; the curation prompt + what-to-remember policy stays in `alyssa`. |

Both repos are cloned locally and will stay that way for the foreseeable future:
- Engine (this repo): `C:\Users\claud\__Code__\chatbot`
- Persona:            `C:\Users\claud\__Code__\alyssa`

---

## Why the split is cheap: the seams already exist

The public/private boundary maps onto extension points already in the codebase:

- **`core/` is model-free** and takes injected callables (`CurateFn`, `SummarizeFn`,
  `retriever`) — memory/knowledge *mechanism* separates cleanly from *who decides
  what to remember*.
- **Prompts are files** loaded by `load_prompt()` — persona is data, not code.
- **Members are a registry** (`MEMBER_BUILDERS`) — specialists are pluggable.
- **Config is code-first** via `configure()` at `main.py` / `main_api.py` —
  deployment is already a thin, separable layer.

Two spots are hardcoded and block a clean split; fix them before publishing:

1. [`core/prompts.py`](../core/prompts.py) — `PROMPTS_DIR` is pinned to
   `repo-root/prompts`. Make it an overlay search path: a configurable private
   dir wins, the bundled demo `prompts/` is the fallback.
2. [`agent/members/__init__.py`](../agent/members/__init__.py) — `MEMBER_BUILDERS`
   is a hardcoded import list. Expose `register_member(builder)` (or entry-points)
   so `alyssa` adds private specialists without editing the public tree.

---

## Target topology

```
<engine>  (public, pip-installable)          alyssa  (private)
├── pyproject.toml   name="<engine>", v0.x   ├── pyproject.toml   deps: <engine>
├── core/                                     ├── prompts/team/
│   ├── memory/     ← mechanism (public)     │   ├── SOUL.md          ← her
│   ├── knowledge/  ← mechanism (public)     │   └── lead.md          ← her routing+rules
│   ├── storage/                             ├── prompts/curation.md  ← what-to-remember policy
│   ├── conversation.py                      ├── alyssa/              ← private members (own namespace)
│   └── prompts.py  ← overlay-aware          ├── main.py              ← configure() + secrets
├── agent/                                    └── .env
│   ├── curator.py  ← CurateFn *mechanism*
│   ├── members/    ← registry + demo specialists
│   └── team.py
├── channels/
├── prompts/        ← neutral DEMO persona (runs out of the box)
└── main.py         ← demo entrypoint
```

Namespace caveat: the engine's top-level imports are generic (`core`, `agent`,
`channels`). In `alyssa`'s venv those names belong to the engine — **do not name
any `alyssa` package `core`/`agent`/`channels`.** Give `alyssa` its own package.

---

## Local development without publishing (do this first)

Use a **uv editable path source**: `alyssa`'s venv links to the engine's working
tree, so every engine edit is live with no reinstall, no version bump, no publish.

### Step 1 — make the engine installable (one-time, `chatbot/pyproject.toml`)

The engine currently has **no `[build-system]`**, so uv treats it as an app, not a
package. Add a backend and declare the flat top-level packages:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["core", "agent", "channels"]
```

(`prompts/` need not be packaged for local dev — see the bonus below.)

### Step 2 — reference it from `alyssa/pyproject.toml`

```toml
[project]
name = "alyssa"
dependencies = [
    "chatbot",          # the engine, by its package name
]

[tool.uv.sources]
chatbot = { path = "../chatbot", editable = true }
```

### Step 3 — wire it up

```powershell
cd C:\Users\claud\__Code__\alyssa
uv sync
```

Now `from core.config import configure`, `from agent.team import build_team`, etc.
resolve to the live engine. Editing engine code needs **no** re-sync; only
changing the engine's *dependencies* or *package set* does.

**Why no republish:** `editable = true` installs a `.pth` pointer to `../chatbot`,
not a snapshot. The engine source tree *is* what alyssa imports.

**Bonus:** `PROMPTS_DIR` is computed from `__file__`, so an editable install
resolves it to the real `chatbot/prompts/` automatically — demo prompts load live
before the overlay seam exists. (That same `__file__` relativity is why the
overlay fix is needed later, so alyssa can supply its *own* prompts.)

### When you eventually publish

Flip one block, no code changes:

```toml
[tool.uv.sources]          # remove, or guard for dev-only
# chatbot = { path = "../chatbot", editable = true }

[project]
dependencies = ["chatbot==0.4.*"]   # pin the published version
```

uv can keep the path source dev-only and the version pin as the real requirement,
so CI uses PyPI while your machine uses the local checkout.

---

## Memory / knowledge split, concretely

Stays on the existing injection seam:

- **Public (mechanism):** `JsonFacts`, the store/index, `apply_ops`, chunking, the
  `CurateFn` *type*, and the manager that applies results. Ships a generic default
  curation prompt.
- **Private (policy):** `alyssa/prompts/curation.md` — the actual
  what-to-remember / tone policy — injected by `build_memory_curator()` reading the
  overlay. The planned memory-management system builds on the public mechanism;
  only the policy prompt is private.

---

## Pre-publish checklist (gate before going public)

1. Land the two seam fixes (prompt overlay + member registry). Safe; keep current
   persona working in place.
2. Move `SOUL.md` + `lead.md` + private members out to `alyssa`; replace with a
   neutral demo persona in the engine.
3. **Scan git _history_ (not just the tree) for secrets and `data/memory/`.** A
   leaked `.env`/token or per-user memory in an old commit survives the split.
   This is the hard gate — if history is dirty, decide between history rewrite vs.
   publishing from a fresh root.
4. Add `LICENSE`, trim README/CONTEXT of persona specifics, tag `v0.1.0`.

---

## Phased roadmap (ordered)

- [ ] **Phase 0 — local link (now).** Steps 1–3 above. Unblocks dual-repo dev with
      zero publishing. No engine behavior change.
- [ ] **Phase 1 — seam fixes.** Prompt overlay + open member registry. Engine still
      runs with the current persona; nothing user-visible changes.
- [ ] **Phase 2 — persona extraction.** Move `SOUL.md`/`lead.md`/private members and
      the curation policy prompt into `alyssa`; add a neutral demo persona to the
      engine. Move the deployment `configure()`/`main.py` into `alyssa`.
- [ ] **Phase 3 — publish gate.** History secret/data scan, LICENSE, README trim,
      first tag. Decide history-rewrite vs. fresh-root.
- [ ] **Phase 4 — flip dependency.** Optionally swap the editable path source for a
      published/pinned version in CI while keeping the local path for dev.

---

## Open items

- **Public engine name.** Still TBD (the private repo is `alyssa`). Candidates
  discussed: `mneme`/`mnemo` (memory), `coterie`/`cadre` (team), `lodestar`. The
  engine's package/distribution name should be chosen before Phase 4 so the
  `alyssa` pin is stable.
- **Build backend.** `hatchling` assumed; `setuptools` works too
  (`[tool.setuptools] packages = ["core","agent","channels"]`).
- **Prompts as wheel data.** Needed only for a non-editable (published) install;
  irrelevant while using the editable path source.
