"""Recipe tools — operator-approved declarative HTTP tools, loaded at startup.

The other half of the evolution loop (magi/core/evolution.py): an approved
`tool` proposal lands as `<memory_dir>/tools-runtime/<name>.json`, and this
loader turns each recipe into a real callable tool at team build. Recipes are
data, not code — method + url_template (+ `{param}` placeholders, query
params, static headers) — so the assistant's capability can grow without
arbitrary code execution ever entering the pipeline.

Execution is deliberately plain: substitute the declared params, make the one
HTTP call, return the (capped) response text. The operator approved the
recipe, so its target host is intentional — that approval is the SSRF
boundary here, mirroring how http_request trusts an explicit user request.
"""

import json
from pathlib import Path
from typing import Annotated, Final

import httpx
from agno.tools import tool
from agno.utils.log import log_info, log_warning
from pydantic import BaseModel, Field

from magi.agent.tools.outputs import ToolOutput, fail, ok
from magi.core.evolution import ProposalError, validate_recipe

_TIMEOUT_S: Final[float] = 30.0
_MAX_BODY_CHARS: Final[int] = 20_000


class RecipeCallData(BaseModel):
    tool: str = Field(description="Which recipe tool ran.")
    url: str = Field(description="The URL that was called (after substitution).")
    status: int = Field(description="HTTP status code.")
    body: str = Field(description="Response text (truncation marked).")


def _build_one(recipe: dict):
    name = str(recipe["name"])
    method = str(recipe["method"]).upper()
    url_template = str(recipe["url_template"])
    param_desc: dict = recipe.get("params") or {}
    headers: dict = recipe.get("headers") or {}

    param_hint = (
        " Parameters: " + "; ".join(f"{k} — {v}" for k, v in param_desc.items())
        if param_desc
        else ""
    )

    @tool(
        name=name,
        description=str(recipe["description"]),
        instructions=(
            f"Operator-approved HTTP recipe: {method} {url_template}."
            f"{param_hint} Pass `params` as a JSON object; names matching a "
            "`{placeholder}` substitute into the URL, the rest ride as query "
            "parameters. Validate the response before relaying it."
        ),
        show_result=True,
    )
    async def recipe_tool(
        params: Annotated[
            dict[str, str],
            Field(default_factory=dict, description="Parameter values (name -> value)."),
        ] = {},  # noqa: B006 — agno reads the annotation default; never mutated.
    ) -> ToolOutput[RecipeCallData]:
        """Run this approved HTTP recipe with the given parameters."""
        url = url_template
        query: dict[str, str] = {}
        for key, value in (params or {}).items():
            placeholder = "{" + key + "}"
            if placeholder in url:
                url = url.replace(placeholder, str(value))
            else:
                query[key] = str(value)
        if "{" in url:
            return fail(f"Missing value(s) for placeholder(s) in {url!r}.")
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=True) as client:
                resp = await client.request(method, url, params=query or None, headers=headers)
        except httpx.HTTPError as exc:
            return fail(f"{name}: request failed ({type(exc).__name__}: {exc}).")
        body = resp.text
        if len(body) > _MAX_BODY_CHARS:
            body = body[:_MAX_BODY_CHARS] + f"\n…[truncated {len(body) - _MAX_BODY_CHARS}+ chars]"
        return ok(
            f"{name}: {resp.status_code}.",
            RecipeCallData(tool=name, url=str(resp.request.url), status=resp.status_code, body=body),
        )

    return recipe_tool


def build_recipe_tools(memory_root: Path) -> list:
    """Every approved recipe under `<memory_root>/tools-runtime`, as tools.
    Invalid files are skipped with a warning — one bad recipe must not cost
    the rest (or the boot)."""
    runtime = Path(memory_root) / "tools-runtime"
    if not runtime.is_dir():
        return []
    tools = []
    for path in sorted(runtime.glob("*.json")):
        try:
            recipe = validate_recipe(path.read_text(encoding="utf-8"))
        except (ProposalError, OSError) as exc:
            log_warning(f"recipes: skipping {path.name}: {exc}")
            continue
        tools.append(_build_one(recipe))
        log_info(f"recipes: tool '{recipe['name']}' loaded from {path.name}")
    return tools


def _parse_recipe_file(path: Path) -> dict:
    """Test seam: parse+validate one recipe file (raises on invalid)."""
    return validate_recipe(json.dumps(json.loads(path.read_text(encoding="utf-8"))))
