"""Entrypoint — run the Discord bot backed by the multimodal agent team."""

from magi.core.config import configure


def apply_deployment_config() -> None:
    """Deployment configuration, in code (secrets stay in .env — see core/config).

    This is THE place to see/change what the bot runs with; defaults for
    everything not listed live in `core.config.Config`.
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
        # Long-running sessions: fold evicted turns into a rolling session
        # summary, keeping the assembled context bounded over a long conversation.
        session_summary=True,
        # Durable memory is owned by the post-turn curator: it rewrites the
        # long-term profile each turn instead of the lead appending facts inline.
        memory_curation=True,
        # Durable object storage — the model's private file/image archive. Off by
        # default. To enable: run an S3-compatible backend (RustFS via
        # docker-compose, see README), `uv sync --extra s3`, set
        # S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY in .env, then uncomment:
        # s3_enabled=True,
        # s3_endpoint_url="http://localhost:9000",  # RustFS; None => AWS S3
    )


def main() -> None:
    apply_deployment_config()

    from magi.channels.discord import build_discord_client

    build_discord_client().serve()


if __name__ == "__main__":
    main()
