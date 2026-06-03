# prompts/

Edit these markdown files to change how the agent behaves. No code change, no
restart-of-your-brain required — just edit and restart the app.

```
prompts/
  system.md          single-agent system prompt (the default brain)
  team/
    lead.md          team router: how the lead delegates to members
    assistant.md     role of the general Assistant member
    researcher.md    role of the Researcher member
```

## How it works

`core/prompts.py` loads each file. The **whole file is the prompt** — write
plain markdown exactly as you want the model to read it. If a file is missing or
empty, a built-in default is used, so the app always runs.

## Precedence (system prompt)

1. `SYSTEM_PROMPT` env var (if set) — wins, for per-deploy overrides
2. `prompts/system.md` — the easy-to-edit default
3. hard-coded fallback in code

So: edit `system.md` for normal changes; set the env var only when a specific
deployment needs to differ.

## Add a new prompt

1. Drop a `.md` file under `prompts/`.
2. Read it where needed: `load_prompt("yourfile.md", "fallback text")`.
