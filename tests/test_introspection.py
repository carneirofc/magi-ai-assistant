"""Tests for the team introspection snapshot (agent/introspect) and its endpoint.

`build_snapshot` is pure over a duck-typed runner, so these run against plain
`SimpleNamespace` stand-ins — no model, no real agno Team. Focus: each tool kind
(function, generic toolkit, MCP toolkit) lands in the right place, and the API
exposes it behind the same bearer gate as the rest of /v1.
"""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from magi.agent.introspect import build_snapshot
from magi.channels.api import create_app
from magi.core.conversation import ConversationReply


def _fn(name, description="", instructions=""):
    """An agno-`Function`-shaped stand-in (has .name/.description/.instructions,
    but no .functions dict, so it's treated as a single callable)."""
    return SimpleNamespace(name=name, description=description, instructions=instructions)


class MCPTools:
    """Duck-named to match `is_mcp_toolkit` (checks the class name in the MRO)."""

    def __init__(self, *, url, connected, functions=None, show_result_tools=None):
        self.name = "seanime"
        self.transport = "streamable-http"
        self.server_params = SimpleNamespace(url=url)
        self.session = object() if connected else None
        self.functions = functions
        self.show_result_tools = show_result_tools or []


def _team(members=None, tools=None, name="ChatbotTeam", lead_id="lead-model"):
    return SimpleNamespace(
        name=name,
        model=SimpleNamespace(id=lead_id),
        members=members or [],
        tools=tools or [],
    )


def test_snapshot_reads_members_roles_models_and_tools():
    member = SimpleNamespace(
        name="Assistant",
        role="general chat",
        model=SimpleNamespace(id="member-model"),
        tools=[_fn("get_current_time", "Return the current time.", "Use for time.")],
    )
    snap = build_snapshot(_team(members=[member], tools=[_fn("http_get")]))

    assert snap.is_team is True
    assert snap.name == "ChatbotTeam"
    assert snap.lead_model == "lead-model"
    assert [m.name for m in snap.members] == ["Assistant"]
    only = snap.members[0]
    assert only.role == "general chat"
    assert only.model == "member-model"
    assert only.tools[0].name == "get_current_time"
    assert only.tools[0].description == "Return the current time."
    assert only.tools[0].instructions == "Use for time."
    assert [t.name for t in snap.team_tools] == ["http_get"]


def test_snapshot_surfaces_mcp_server_and_its_tools():
    mcp = MCPTools(
        url="http://127.0.0.1:43211/api/v1/mcp",
        connected=True,
        functions={"search_anime": _fn("search_anime"), "get_anime": _fn("get_anime")},
    )
    member = SimpleNamespace(name="Anime", role="anime", model=SimpleNamespace(id="m"), tools=[mcp])
    snap = build_snapshot(_team(members=[member]))

    assert len(snap.mcp_servers) == 1
    server = snap.mcp_servers[0]
    assert server.name == "seanime"
    assert server.transport == "streamable-http"
    assert server.url == "http://127.0.0.1:43211/api/v1/mcp"
    assert server.connected is True
    assert server.member == "Anime"
    assert server.tools == ["search_anime", "get_anime"]
    # The MCP tools also show up in the member's tool list, tagged by source.
    assert {t.name for t in snap.members[0].tools} == {"search_anime", "get_anime"}
    assert all(t.source == "mcp:seanime" for t in snap.members[0].tools)


def test_snapshot_falls_back_to_show_result_tools_when_unconnected():
    """An unconnected MCP toolkit has no discovered functions yet — the tools we
    asked it to surface stand in so the roster isn't blank before first connect."""
    mcp = MCPTools(url="http://x/mcp", connected=False, show_result_tools=["search_anime"])
    snap = build_snapshot(_team(members=[
        SimpleNamespace(name="Anime", role="anime", model=SimpleNamespace(id="m"), tools=[mcp]),
    ]))

    assert snap.mcp_servers[0].connected is False
    assert snap.mcp_servers[0].tools == ["search_anime"]


def test_snapshot_expands_generic_toolkit_into_its_functions():
    toolkit = SimpleNamespace(
        name="DockerTools",
        functions={"run_container": _fn("run_container", "Run a container.")},
    )
    snap = build_snapshot(_team(tools=[toolkit]))

    names = {t.name: t for t in snap.team_tools}
    assert "run_container" in names
    assert names["run_container"].source == "toolkit:DockerTools"
    assert not snap.mcp_servers


def test_snapshot_handles_bare_single_agent_runner():
    # No `members` attribute at all → treated as a single agent.
    agent = SimpleNamespace(name="Solo", model=SimpleNamespace(id="agent-model"), tools=[_fn("ping")])
    snap = build_snapshot(agent)

    assert snap.is_team is False
    assert snap.lead_model == "agent-model"
    assert [t.name for t in snap.team_tools] == ["ping"]


def test_snapshot_of_none_is_empty():
    snap = build_snapshot(None)
    assert snap.is_team is False
    assert snap.members == []
    assert snap.team_tools == []


# --- endpoint ----------------------------------------------------------------
class _FakeConversation:
    """Minimal ConversationService stand-in exposing a `runner` to introspect."""

    def __init__(self, runner):
        self.runner = runner

    async def handle(self, **_):  # pragma: no cover - unused here
        return ConversationReply(text="")


def test_introspection_endpoint_returns_the_snapshot():
    member = SimpleNamespace(
        name="Assistant", role="general chat", model=SimpleNamespace(id="m"), tools=[_fn("ping")]
    )
    client = TestClient(create_app(_FakeConversation(_team(members=[member]))))

    resp = client.get("/v1/introspection")

    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "ChatbotTeam"
    assert body["lead_model"] == "lead-model"
    assert body["members"][0]["name"] == "Assistant"
    assert body["members"][0]["tools"][0]["name"] == "ping"


def test_introspection_endpoint_requires_auth_when_token_set():
    client = TestClient(create_app(_FakeConversation(_team()), auth_token="secret"))

    assert client.get("/v1/introspection").status_code == 401
    ok = client.get("/v1/introspection", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200


# --- capability origins (the by-origin roster grouping) ----------------------
def test_snapshot_reads_origin_stamps():
    from magi.agent.introspect import mark_origin

    def approved_recipe():
        """An operator-approved HTTP recipe tool."""

    def engine_tool():
        """A built-in."""

    mark_origin([approved_recipe], "recipe")
    runner = SimpleNamespace(
        name="T", model=SimpleNamespace(id="lead"), tools=[approved_recipe, engine_tool],
        members=None,
    )

    snapshot = build_snapshot(runner)
    origins = {t.name: t.origin for t in snapshot.team_tools}
    assert origins["approved_recipe"] == "recipe"
    assert origins["engine_tool"] == "builtin"  # unstamped defaults honestly


def test_team_build_stamps_recipe_registered_and_skill_origins(tmp_path):
    import json as _json
    from dataclasses import fields

    from agno.tools import tool

    from magi.agent.skills import SKILLS, Skill, register_skill
    from magi.agent.team import build_team
    from magi.agent.tools import LEAD_TOOLKIT_BUILDERS, register_lead_toolkit
    from magi.core.config import config, configure
    from magi.core.memory import build_memory_from_config

    @tool(name="skill_tool", show_result=True)
    def skill_tool():
        """From a skill manifest."""

    @tool(name="persona_tool", show_result=True)
    def persona_tool():
        """From a registered persona toolkit."""

    # An operator-APPROVED recipe already on disk in the runtime dir.
    recipes = tmp_path / "memory" / "tools-runtime"
    recipes.mkdir(parents=True)
    (recipes / "fetch_weather.json").write_text(
        _json.dumps(
            {
                "name": "fetch_weather",
                "description": "Local weather.",
                "method": "GET",
                "url_template": "http://127.0.0.1:9000/weather",
            }
        ),
        encoding="utf-8",
    )

    skills_snapshot = list(SKILLS)
    toolkit_snapshot = list(LEAD_TOOLKIT_BUILDERS)
    config_snapshot = {f.name: getattr(config, f.name) for f in fields(config)}
    register_skill(Skill(name="demo", prompt="demo skill", tools=(skill_tool,)))
    register_lead_toolkit(lambda memory: [persona_tool])
    try:
        configure(memory_dir=str(tmp_path / "memory"), evolution_enabled=True)
        team = build_team(build_memory_from_config())
    finally:
        SKILLS[:] = skills_snapshot
        LEAD_TOOLKIT_BUILDERS[:] = toolkit_snapshot
        configure(**config_snapshot)

    origins = {t.name: t.origin for t in build_snapshot(team).team_tools}
    assert origins["fetch_weather"] == "recipe"
    assert origins["persona_tool"] == "registered"
    assert origins["skill_tool"] == "skill"
    assert origins["agent_introspection"] == "builtin"
