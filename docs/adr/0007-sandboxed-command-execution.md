# 0007 — Sandboxed command execution

- Status: Accepted
- Date: 2026-09-27
- Builds on: ADR 0005 (one brain, many channels), the deny-by-default rule
  for chat platforms

## Context

A shell tool turns the assistant from "talks about work" into "does work",
and is the most dangerous capability it can have: the model can be steered
by untrusted content (web pages, files, other chat users) into running
anything. It must be useful on a trusted personal box without being a remote
shell for whoever can reach a channel.

## Threat model

- **Prompt injection** makes the model *want* to run a harmful command.
- **Other chat users** (a shared Discord guild) try to use the tool.
- **The model approving itself**: it must not be able to confirm its own
  risky command.
- **Secrets** in the magi process environment (API tokens) must not leak to
  commands.
- **Resource abuse**: fork bombs, runaway output, never-ending commands.

## Decision

1. **Off by default** (`sandbox.backend: off`). `docker` runs each command in
   a throwaway container: no network, read-only root, tmpfs `/tmp`,
   `cap_drop: ALL`, `no-new-privileges`, host uid:gid, memory/pid/CPU limits,
   only the user's workspace mounted. `local` has *no* isolation (doctor
   warns) but still scrubs the environment, applies rlimits, and kills the
   process group on timeout.
2. **Deny by default for users**: only scoped ids in `sandbox.allowed_users`
   may run anything.
3. **A policy seatbelt** (`policy.classify`) — *not* the security boundary:
   `forbidden` (sudo, `rm -rf /`, disk writes, pipe-to-shell, fork bombs)
   never runs; `dangerous` (deletes, installs, network, paths outside the
   workspace, command substitution, anything unparsable) needs approval per
   `sandbox.approval`.
4. **Approval is a user message**: the tool returns an id; only the *same
   user* sending `/approve <id>` (or `/deny <id>`) within the TTL resolves
   it. `ConversationService` handles it before the model runs, so the model
   cannot forge it and every channel supports it. The outcome becomes that
   turn's input, so the model continues the task.
5. **Audit**: every request and decision is appended to
   `$MAGI_HOME/logs/exec.jsonl`.

## Consequences

- Safe for a single operator on the docker backend; a shared-guild deployment
  must keep `allowed_users` tight.
- Approvals live in memory: a restart drops pending ones.
- The pre-existing opt-in `Docker` team member (agno `DockerTools`) is a
  separate, unsandboxed capability and stays out of the default roster.
