"""Entrypoint — the admin API, run inside a container.

    python main_admin_docker.py      # (the admin image's CMD)

Same surface as main_admin.py; this only overlays the settings that differ in a
container (still in code — no new env vars, see core/config). The shared
deployment config is reused verbatim from main_admin.apply_deployment_config().
"""

from magi.core.config import configure
from main_admin import apply_deployment_config


def apply_container_overrides() -> None:
    """The container-only deltas from the host deployment."""
    configure(
        # Bind every interface so the BFF container can reach this one over the
        # compose network. The port is still NOT published to the host (see the
        # compose service) — only `web` is reachable from outside.
        admin_host="0.0.0.0",
        # Backends run on the host; Docker maps it under host.docker.internal
        # (compose: extra_hosts host.docker.internal:host-gateway).
        llamacpp_base_url="http://host.docker.internal:8888/v1",
        qdrant_url="http://host.docker.internal:6333",
    )


def main() -> None:
    apply_deployment_config()
    apply_container_overrides()

    import uvicorn

    from magi.channels.admin import build_admin_app
    from magi.core.config import config

    uvicorn.run(build_admin_app(), host=config.admin_host, port=config.admin_port)


if __name__ == "__main__":
    main()
