"""The single entrypoint — pick a channel, optionally with container overrides.

    python main.py api                # HTTP API service (default)
    python main.py discord            # Discord bot
    python main.py discord --check    # Discord Phase 1 (raw connect, no agno)
    python main.py admin              # operator admin API (memory + knowledge)
    python main.py desktop            # frameless native shell for the web frontend

    # inside a container, add --docker to overlay the container-only deltas
    # (bind 0.0.0.0, reach host services via host.docker.internal):
    python main.py api --docker
    python main.py discord --docker
    python main.py admin --docker

This is THE place to see/change what each channel runs with. All settings are
set in code below via configure(); only secrets (API_AUTH_TOKEN, DISCORD_BOT_TOKEN,
ADMIN_AUTH_TOKEN, QDRANT_API_KEY, …) come from .env (see core/config). Defaults
for everything not listed live in core.config.Config.
"""

import argparse
import sys

from magi.core.config import configure


# --- shared brain: the model + memory stack every chat channel serves ----------


def _configure_brain() -> None:
    """The model/session/memory config shared by the chat channels (api, discord).

    The admin API never runs the model, so it does NOT call this — see
    configure_admin().
    """
    configure(
        # llama.cpp llama-server on :8888 serves chat for lead + members.
        model_provider="llamacpp",
        llamacpp_base_url="http://127.0.0.1:8888/v1",
        lead_model_id="qwen3.5-9b",  # cosmetic on the direct path (one model per server)
        member_model_id="qwen3.5-9b",
        # Budget for context assembly — keep equal to llama-server --ctx-size.
        lead_num_ctx=128_000,
        member_num_ctx=128_000,
        # Sampling: send nothing, the server's launch flags carry the model's
        # recommended settings (temp 0.6, top_p 0.95, top_k 20, min_p 0).
        model_temperature=None,
        # Thinking off: this finetune leaks tool calls inside unclosed think
        # blocks, breaking delegation. The lead's set_thinking tool can flip
        # this at runtime.
        model_extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        # Long-running sessions: fold turns that roll out of the live window into
        # a rolling session summary so context stays bounded over a long conversation.
        session_summary=True,
        # Durable memory is owned by the post-turn curator: it rewrites the
        # long-term profile each turn instead of the lead appending facts inline.
        memory_curation=True,
        # Durable object storage — the model's private file/image archive (it can
        # decide to stash a file and recall it later). Off by default. To enable:
        # run an S3-compatible backend (RustFS via docker-compose, see README),
        # `uv sync --extra s3`, set S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY in
        # .env, then uncomment:
        # s3_enabled=True,
        # s3_endpoint_url="http://localhost:9000",  # RustFS; None => AWS S3
        # s3_bucket="chatbot-memory",
    )


def _brain_docker_overrides() -> None:
    """The container-only deltas from the host brain config (bind + host reach)."""
    configure(
        # llama-server runs on the host, not in this container. Docker maps the
        # host under this name (compose: extra_hosts host.docker.internal:host-gateway).
        # Other backends (litellm/qdrant/rustfs) reach the host the same way if
        # enabled — override them here too.
        llamacpp_base_url="http://host.docker.internal:8888/v1",
    )


# --- per-channel deployment config --------------------------------------------


def configure_api(docker: bool) -> None:
    """The HTTP API service: the shared brain plus its HTTP binding."""
    _configure_brain()
    configure(
        # HTTP binding for this service. Bind 0.0.0.0 only behind a trusted proxy
        # and with API_AUTH_TOKEN set.
        api_host="127.0.0.1",
        api_port=8000,
        # Web clients: list the browser origins allowed to call /v1 (CORS). Empty
        # = same-origin / non-browser only. Use ["*"] to allow any origin (safe
        # here — auth is a Bearer token, not a cookie). Set the real app origins
        # for a production web UI, e.g. ["https://app.example.com"].
        api_cors_origins=["*"],
        # Anime specialist source. False (default) = the hand-rolled Seanime HTTP
        # tools. True = Seanime's built-in read-only MCP server (enable it there
        # via experimental.mcp, and `uv sync --extra mcp`). One anime member either way.
        # seanime_use_mcp=True,
        # Admin surface (memory + knowledge management, see channels/admin.py)
        # mounted onto THIS SAME app under /admin/v1/* instead of running the admin
        # channel separately — one process, one port. Uncomment and set an
        # ADMIN_AUTH_TOKEN in .env if this ever leaves localhost:
        # admin_enabled=True,
    )
    if docker:
        _brain_docker_overrides()
        configure(
            # Bind every interface so the published port is reachable from the host
            # (127.0.0.1 inside a container is the container's own loopback).
            api_host="0.0.0.0",
        )


def configure_discord(docker: bool) -> None:
    """The Discord bot: the shared brain, served outbound over the gateway."""
    _configure_brain()
    configure(
        # Admin surface (memory + knowledge management, see channels/admin.py) in
        # this SAME process instead of running the admin channel separately — one
        # process, no BFF required for local/single-operator use. Starts a second
        # uvicorn server on admin_host:admin_port alongside the gateway connection
        # (see channels/discord.py: serve_with_admin). Uncomment and set an
        # ADMIN_AUTH_TOKEN in .env if this ever leaves localhost:
        # admin_enabled=True,
    )
    if docker:
        _brain_docker_overrides()


def configure_admin(docker: bool) -> None:
    """The operator admin API: reuses the chat stack's backends, never runs the model."""
    configure(
        # Same brain's backends — the admin service reuses the chat stack's Qdrant
        # + embedding proxy to read/manage the corpus; it never runs the model.
        model_provider="llamacpp",
        llamacpp_base_url="http://127.0.0.1:8888/v1",
        # Admin HTTP binding. Keep localhost / the port unpublished: the Next.js
        # BFF is the only intended caller and it holds ADMIN_AUTH_TOKEN server-side.
        admin_host="127.0.0.1",
        admin_port=8100,
    )
    if docker:
        configure(
            # Bind every interface so the BFF container can reach this one over the
            # compose network. The port is still NOT published to the host.
            admin_host="0.0.0.0",
            # Backends run on the host; Docker maps it under host.docker.internal.
            llamacpp_base_url="http://host.docker.internal:8888/v1",
            qdrant_url="http://host.docker.internal:6333",
        )


def configure_desktop(no_frameless: bool) -> None:
    """The desktop shell: render the web frontend AND serve its backend, in one command.

    The shell launches the SAME Next.js frontend as the browser (web/) as a
    loopback child and shows it in a frameless Qt window. Its pages proxy to the
    chat-api (/v1/*) and admin-api (/admin/v1/*), so — so that dashboard, memory,
    knowledge, subjects, team and chat are all live out of the box — the shell also
    runs that API IN-PROCESS: the shared brain plus `admin_enabled=True`, i.e. the
    single-app shape that serves /v1/* AND /admin/v1/* (ADR 0002). It binds a
    loopback ephemeral port the shell wires the BFF to; the model backend
    (llama-server) only needs to be up for actual chat turns — the admin pages work
    regardless. Set `desktop_serve_backend=False` to reuse a backend running
    elsewhere. Desktop-only knobs live under `desktop_*` in core.config.
    """
    _configure_brain()
    configure(
        # One in-process app serves BOTH the chat-api and the admin surface, so the
        # frontend's admin pages (dashboard/memory/knowledge/subjects/persona) and
        # its chat/team pages all resolve against a single loopback backend.
        desktop_serve_backend=True,
        admin_enabled=True,
        # None => auto-resolve web/ (repo tree from source, <_MEIPASS>/web when frozen).
        desktop_web_dir=None,
        # Node executable that runs the frontend server (on PATH by default).
        desktop_node_command="node",
        # Route the window opens on (the chat console). The frontend's auth gate
        # bounces an unauthenticated first launch to /login; the session cookie
        # then persists in the window's profile.
        desktop_start_path="/chat",
    )
    if no_frameless:
        # Debug aid: a normal titled window (no translucency/drag) around the same
        # web content — bridge + server behave identically.
        configure(desktop_frameless=False)


# --- channel runners ----------------------------------------------------------


def run_api() -> None:
    import uvicorn

    from magi.channels.api import build_api_app
    from magi.core.config import config

    uvicorn.run(build_api_app(), host=config.api_host, port=config.api_port)


def run_admin() -> None:
    import uvicorn

    from magi.channels.admin import build_admin_app
    from magi.core.config import config

    uvicorn.run(build_admin_app(), host=config.admin_host, port=config.admin_port)


def run_desktop() -> None:
    # Imported lazily so the optional `desktop` extra (PySide6) is only needed
    # when this channel is actually run — `python main.py api` stays Qt-free.
    from magi.desktop import run_desktop as _run_desktop

    sys.exit(_run_desktop())


def run_discord(check: bool) -> None:
    from magi.core.config import config

    if check:
        # Phase 1 — raw discord.py, no agno. See channels/discord_check.py.
        import asyncio

        from magi.channels import discord_check

        if not config.DISCORD_BOT_TOKEN:
            print("ERROR: DISCORD_BOT_TOKEN not set in environment")
            sys.exit(1)
        asyncio.run(discord_check.run(config.DISCORD_BOT_TOKEN))
        return

    # Phase 2 — full agno integration via DiscordClient.
    from magi.channels.discord import build_discord_client, serve_with_admin

    discord_client = build_discord_client()
    if config.admin_enabled:
        serve_with_admin(discord_client)
    else:
        discord_client.serve()


# --- dispatch -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a magi channel.")
    sub = parser.add_subparsers(dest="channel")

    api = sub.add_parser("api", help="HTTP API service (default)")
    api.add_argument("--docker", action="store_true", help="apply container-only overrides")

    discord = sub.add_parser("discord", help="Discord bot")
    discord.add_argument("--docker", action="store_true", help="apply container-only overrides")
    discord.add_argument(
        "--check", action="store_true", help="Phase 1: raw connect, no agno (see discord_check)"
    )

    admin = sub.add_parser("admin", help="operator admin API (memory + knowledge)")
    admin.add_argument("--docker", action="store_true", help="apply container-only overrides")

    desktop = sub.add_parser("desktop", help="frameless native shell for the web frontend")
    desktop.add_argument(
        "--no-frameless",
        action="store_true",
        help="use a normal titled window (debugging the web content)",
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()
    channel = args.channel or "api"  # default to the HTTP API

    if channel == "api":
        configure_api(args.docker)
        run_api()
    elif channel == "discord":
        configure_discord(args.docker)
        run_discord(args.check)
    elif channel == "admin":
        configure_admin(args.docker)
        run_admin()
    elif channel == "desktop":
        configure_desktop(args.no_frameless)
        run_desktop()


if __name__ == "__main__":
    main()
