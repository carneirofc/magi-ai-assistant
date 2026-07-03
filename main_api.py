"""Entrypoint — serve the agent as a standalone HTTP service.

    python main_api.py

External clients (desktop app, web UI, ...) talk to it over plain HTTP/JSON —
see channels/api.py for the endpoint contract. All settings are set in code
below; only secrets (API_AUTH_TOKEN, ...) come from .env.
"""

from magi.core.config import Config


def apply_deployment_config() -> Config:
    """Deployment configuration, in code (secrets stay in .env — see core/config).

    Returns the immutable `Config` the composition root threads through
    `AgentContext` — no process global."""
    return Config(
        # Same brain as the Discord bot: llama-server on :8888.
        model_provider="llamacpp",
        llamacpp_base_url="http://127.0.0.1:8888/v1",
        lead_model_id="qwen3.5-9b",
        member_model_id="qwen3.5-9b",
        lead_num_ctx=128_000,  # == llama-server --ctx-size
        member_num_ctx=128_000,
        model_temperature=None,  # defer to llama-server launch flags
        model_extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        # Long-running sessions: fold turns that roll out of the live window into
        # a rolling session summary so context stays bounded. Durable memory is
        # owned by the post-turn curator (rewrites the long-term profile each turn).
        session_summary=True,
        memory_curation=True,
        # HTTP binding for this service. Bind 0.0.0.0 only behind a trusted proxy
        # and with API_AUTH_TOKEN set.
        api_host="127.0.0.1",
        api_port=8000,
        # Web clients: list the browser origins allowed to call /v1 (CORS). Empty
        # = same-origin / non-browser only. Use ["*"] to allow any origin (safe
        # here — auth is a Bearer token, not a cookie). Set the real app origins
        # for a production web UI, e.g. ["https://app.example.com"].
        api_cors_origins=["*"],
        # Durable object storage — the model's private file/image archive (it can
        # decide to stash a file and recall it later). Off by default. To turn on,
        # run an S3-compatible backend (RustFS via docker-compose, see README),
        # install boto3 (`uv sync --extra s3`), put S3_ACCESS_KEY_ID /
        # S3_SECRET_ACCESS_KEY in .env, and uncomment:
        # s3_enabled=True,
        # s3_endpoint_url="http://localhost:9000",  # RustFS; None => AWS S3
        # s3_bucket="chatbot-memory",
        # Anime specialist source. False (default) = the hand-rolled Seanime HTTP
        # tools. True = Seanime's built-in read-only MCP server at
        # <seanime_base_url>/api/v1/mcp (enable it there via experimental.mcp, and
        # install the optional extra: `uv sync --extra mcp`). The app opens the MCP
        # connection at startup. One anime member either way.
        # seanime_use_mcp=True,
        # Admin surface (memory + knowledge management, see channels/admin.py)
        # mounted onto THIS SAME app under /admin/v1/* instead of running
        # main_admin.py separately — one process, one port (admin_host/
        # admin_port are unused in this mode). Uncomment and set an
        # ADMIN_AUTH_TOKEN in .env if this ever leaves localhost:
        # admin_enabled=True,
    )


def main() -> None:
    config = apply_deployment_config()

    import uvicorn

    from magi.channels.api import build_api_app
    from magi.core.context import AgentContext

    ctx = AgentContext(config=config)
    uvicorn.run(build_api_app(ctx), host=config.api_host, port=config.api_port)


if __name__ == "__main__":
    main()
