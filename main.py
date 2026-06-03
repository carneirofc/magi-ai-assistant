"""Entrypoint. M1: serve the OpenAI-compatible API for OpenWebUI."""

import uvicorn

from core.config import config


def main() -> None:
    uvicorn.run("channels.openai_api:app", host=config.host, port=config.port)


if __name__ == "__main__":
    main()
