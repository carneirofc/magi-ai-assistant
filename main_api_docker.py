"""Entrypoint — the HTTP service, run inside a container.

    python main_api_docker.py      # (this is the image's default CMD)

Same brain as main_api.py; this only overlays the two settings that differ in a
container (still in code — no new env vars, see core/config). The shared
deployment config is reused verbatim from main_api.apply_deployment_config().
"""

from magi.core.config import configure
from main_api import apply_deployment_config


def apply_container_overrides() -> None:
    """The container-only deltas from the host deployment."""
    configure(
        # Bind every interface so the published port is reachable from the host
        # (127.0.0.1 inside a container is the container's own loopback).
        api_host="0.0.0.0",
        # llama-server runs on the host, not in this container. Docker maps the
        # host under this name (compose: extra_hosts host.docker.internal:host-gateway).
        # Other backends (litellm/qdrant/rustfs) reach the host the same way if
        # enabled — override them here too.
        llamacpp_base_url="http://host.docker.internal:8888/v1",
    )


def main() -> None:
    apply_deployment_config()
    apply_container_overrides()

    import uvicorn

    from magi.channels.api import build_api_app
    from magi.core.config import config

    uvicorn.run(build_api_app(), host=config.api_host, port=config.api_port)


if __name__ == "__main__":
    main()
