"""Provider-agnostic model factory.

`build_model` is the one place that knows how to instantiate each provider's
model. Defaults come from config; every argument can be overridden per call so
behavior is parameterized rather than hard-coded.
"""

import enum
import pydantic

from agno.models.base import Model
from agno.utils.log import log_info

from core.config import config

class ModelProviderEnum(enum.StrEnum):
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"

class ModelDefinition(pydantic.BaseModel):
    provider: ModelProviderEnum
    model_id: str
    has_tools: bool = False


# Models:
# class ModelsEnum(enum.StrEnum):
#     OLLAMA_SECOND_CONSTANTINE_GPT_OSS_U_20B = "second_constantine/gpt-oss-u:20b"
#     OLLAMA_GEMMA_4_26B = "gemma4:26b"
#     # lfm2.5:latest
#     OLLAMA_LFM_25_LATEST = "lfm2.5:latest"
#     OLLAMA_DOLPHIN_MIXTRAL_8X7B = "dolphin-mixtral:8x7b"
#     OLLAMA_GEMMA_4_E4B = "gemma4:e4b"

#     # lfm2.5:latest
#     # ollama run gemma4:26b

#     # DATABRICKS_GPT_OSS_120B = "databricks-gpt-oss-120b"
#     DATABRICKS_CLAUDE_SONNET_4_6 = "databricks-claude-sonnet-4-6"
# # export ANTHROPIC_BASE_URL="https://<workspace>.azuredatabricks.net/serving-endpoints/anthropic"
# # export ANTHROPIC_AUTH_TOKEN="dapi-REDACTED"
# # export ANTHROPIC_MODEL="databricks-claude-sonnet-4-6"
# # export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1


def build_model(model: ModelDefinition) -> Model:
    log_info(
        f"building model: {model}, "
    )
    if model.provider == ModelProviderEnum.ANTHROPIC:
        from agno.models.anthropic import Claude

        client_params = {}
        if config.anthropic_base_url:
            client_params["base_url"] = config.anthropic_base_url
        return Claude(
            id=model.model_id,
            api_key=config.anthropic_api_key,
            auth_token=config.anthropic_auth_token,
            client_params=client_params or None,
        )

    if model.provider == ModelProviderEnum.OLLAMA:
        from agno.models.ollama import Ollama

        return Ollama(id=model.model_id, host=config.ollama_host)

    raise ValueError(f"unsupported model provider: {model.provider!r}")
    


# def build_model(provider: str | None = None, model_id: str | None = None) -> Model:
#     """Build a model. Defaults from config; override per call."""
#     provider = (provider or config.model_provider).lower()
#     model_id = model_id or config.model_id
#     log_info(
#         f"building model: provider={provider}, id={model_id}, "
#         f"anthropic_base_url={'custom' if config.anthropic_base_url else 'default'}"
#     )
#     if provider == "anthropic":
#         from agno.models.anthropic import Claude

#         client_params = {}
#         if config.anthropic_base_url:
#             client_params["base_url"] = config.anthropic_base_url
#         return Claude(
#             id=model_id,
#             api_key=config.anthropic_api_key,
#             auth_token=config.anthropic_auth_token,
#             client_params=client_params or None,
#         )
#     if provider == "ollama":
#         from agno.models.ollama import Ollama

#         return Ollama(id=model_id, host=config.ollama_host)
#     raise ValueError(f"Unknown MODEL_PROVIDER: {provider!r}")
