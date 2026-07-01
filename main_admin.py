"""Entrypoint — serve the operator admin API as a standalone HTTP service.

    python main_admin.py

The management surface for memory + knowledge (see channels/admin.py). A separate
process from the chat API; reached only through the Next.js BFF (web/). All
settings are set in code below; only secrets (ADMIN_AUTH_TOKEN, QDRANT_API_KEY,
...) come from .env.
"""

from magi.core.config import configure


def apply_deployment_config() -> None:
    """Deployment configuration, in code (secrets stay in .env — see core/config)."""
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


def main() -> None:
    apply_deployment_config()

    import uvicorn

    from magi.channels.admin import build_admin_app
    from magi.core.config import config

    uvicorn.run(build_admin_app(), host=config.admin_host, port=config.admin_port)


if __name__ == "__main__":
    main()
