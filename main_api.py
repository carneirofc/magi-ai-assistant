"""Entrypoint — serve the agent as a standalone HTTP service.

    python main_api.py

External clients (desktop app, web UI, ...) talk to it over plain HTTP/JSON —
see channels/api.py for the endpoint contract. All settings are set in code
below; only secrets (API_AUTH_TOKEN, ...) come from .env.
"""

from core.config import configure


def apply_deployment_config() -> None:
    """Deployment configuration, in code (secrets stay in .env — see core/config)."""
    configure(
        # Same brain as the Discord bot: llama-server on :8080.
        model_provider="llamacpp",
        llamacpp_base_url="http://localhost:8080/v1",
        lead_model_id="qwen3.5-9b",
        member_model_id="qwen3.5-9b",
        lead_num_ctx=128_000,  # == llama-server --ctx-size
        member_num_ctx=128_000,
        model_temperature=None,  # defer to llama-server launch flags
        model_extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        # HTTP binding for this service.
        api_host="127.0.0.1",
        api_port=8000,
    )


def main() -> None:
    apply_deployment_config()

    import uvicorn

    from channels.api import build_api_app
    from core.config import config

    uvicorn.run(build_api_app(), host=config.api_host, port=config.api_port)


if __name__ == "__main__":
    main()
