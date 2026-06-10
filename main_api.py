"""Entrypoint — serve the agent as a standalone HTTP service.

    python main_api.py

External clients (desktop app, web UI, ...) talk to it over plain HTTP/JSON —
see channels/api.py for the endpoint contract. Host/port/auth come from config
(API_HOST, API_PORT, API_AUTH_TOKEN).
"""


def main() -> None:
    import uvicorn

    from channels.api import build_api_app
    from core.config import config

    uvicorn.run(build_api_app(), host=config.api_host, port=config.api_port)


if __name__ == "__main__":
    main()
