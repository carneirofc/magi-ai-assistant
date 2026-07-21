"""Tests for the self-evolution approval queue (core/evolution): the propose
rails, the one-shot decide/apply path, recipe validation + the runtime tool
loader, and the admin endpoints."""

import json

import pytest
from fastapi.testclient import TestClient

from magi.core.config import config, configure
from magi.core.evolution import EvolutionStore, ProposalError, validate_recipe


def _store(tmp_path, **kwargs) -> EvolutionStore:
    return EvolutionStore(tmp_path / "memory", proposable=["curation.md", "greet.md"], **kwargs)


# --- propose rails -------------------------------------------------------------


def test_propose_prompt_respects_the_allowlist(tmp_path):
    store = _store(tmp_path)

    proposal = store.propose(
        "prompt", "curation.md", "New curation policy text.", "it over-saves", source="lead"
    )
    assert proposal.status == "pending" and store.get(proposal.id) is not None

    with pytest.raises(ProposalError):  # identity prompts are not proposable
        store.propose("prompt", "team/SOUL.md", "x" * 30, "drift attempt", source="lead")
    with pytest.raises(ProposalError):  # traversal-shaped targets rejected outright
        store.propose("prompt", "../secrets.md", "x" * 30, "nope", source="lead")


def test_propose_queue_is_capped(tmp_path):
    store = _store(tmp_path, queue_max=2)
    store.propose("prompt", "curation.md", "v1 text here", "r1", source="lead")
    store.propose("prompt", "greet.md", "v2 text here", "r2", source="lead")
    with pytest.raises(ProposalError):
        store.propose("prompt", "curation.md", "v3 text here", "r3", source="lead")


def test_recipe_validation_rails():
    good = validate_recipe(
        json.dumps(
            {
                "name": "fetch_weather",
                "description": "Local weather",
                "method": "get",
                "url_template": "http://127.0.0.1:9000/weather?city={city}",
            }
        )
    )
    assert good["name"] == "fetch_weather"

    for bad in (
        "not json",
        json.dumps({"name": "x y", "description": "d", "method": "GET", "url_template": "http://a"}),
        json.dumps({"name": "ok_name", "description": "d", "method": "YOLO", "url_template": "http://a"}),
        json.dumps({"name": "ok_name", "description": "d", "method": "GET", "url_template": "ftp://a"}),
    ):
        with pytest.raises(ProposalError):
            validate_recipe(bad)


# --- decide / apply ---------------------------------------------------------------


def test_approve_writes_the_runtime_overlay_and_is_one_shot(tmp_path):
    store = _store(tmp_path)
    p = store.propose("prompt", "greet.md", "Greet warmly, mention due reminders.", "r", source="lead")

    decided = store.decide(p.id, approve=True)

    assert decided.status == "approved"
    runtime_file = store.prompts_runtime / "greet.md"
    assert runtime_file.read_text(encoding="utf-8").startswith("Greet warmly")
    with pytest.raises(ProposalError):  # decisions are one-shot
        store.decide(p.id, approve=False)


def test_reject_applies_nothing(tmp_path):
    store = _store(tmp_path)
    p = store.propose("prompt", "greet.md", "Something else entirely.", "r", source="lead")

    decided = store.decide(p.id, approve=False)

    assert decided.status == "rejected"
    assert not (store.prompts_runtime / "greet.md").exists()
    assert store.decide("unknown-id", approve=True) is None


def test_approved_tool_recipe_lands_and_loads(tmp_path):
    from magi.agent.tools.recipes import build_recipe_tools

    store = _store(tmp_path)
    recipe = json.dumps(
        {
            "name": "fetch_weather",
            "description": "Local weather service",
            "method": "GET",
            "url_template": "http://127.0.0.1:9000/weather?city={city}",
            "params": {"city": "City name"},
        }
    )
    p = store.propose("tool", "fetch_weather", recipe, "asked for weather daily", source="lead")
    store.decide(p.id, approve=True)

    tools = build_recipe_tools(tmp_path / "memory")

    assert [t.name for t in tools] == ["fetch_weather"]
    # A corrupt runtime file is skipped, not fatal.
    (store.tools_runtime / "broken.json").write_text("{nope", encoding="utf-8")
    assert [t.name for t in build_recipe_tools(tmp_path / "memory")] == ["fetch_weather"]


# --- admin endpoints -----------------------------------------------------------------


def _admin_client(tmp_path):
    from magi.channels.admin import create_admin_app
    from magi.core.knowledge import SubjectRegistry
    from magi.core.memory.store import FileMemoryStore

    class _NoKnowledge:
        def list_documents(self):
            return []

    store = FileMemoryStore(tmp_path / "memory")
    app = create_admin_app(_NoKnowledge(), store, SubjectRegistry(tmp_path / "subjects.json"))
    return TestClient(app)


def test_proposal_endpoints_gate_on_the_feature_flag(tmp_path):
    client = _admin_client(tmp_path)
    assert client.get("/admin/v1/proposals").status_code == 503  # evolution off


def test_proposal_endpoints_roundtrip(tmp_path):
    client = _admin_client(tmp_path)
    evo = EvolutionStore(tmp_path / "memory", proposable=["greet.md"])
    p = evo.propose("prompt", "greet.md", "Different greeting policy.", "r", source="lead")

    old = (config.evolution_enabled, config.evolution_proposable)
    configure(evolution_enabled=True, evolution_proposable=["greet.md"])
    try:
        body = client.get("/admin/v1/proposals").json()
        assert [x["id"] for x in body["proposals"]] == [p.id]
        assert body["proposable"] == ["greet.md"]

        approved = client.post(f"/admin/v1/proposals/{p.id}/approve").json()
        assert approved["status"] == "approved" and approved["applied_path"]

        assert client.post(f"/admin/v1/proposals/{p.id}/reject").status_code == 409
        assert client.post("/admin/v1/proposals/nope/approve").status_code == 404
    finally:
        configure(evolution_enabled=old[0], evolution_proposable=old[1])


def test_proposal_endpoints_list_registered_skills_as_proposable(tmp_path):
    from magi.agent.skills import SKILLS, Skill, register_skill

    client = _admin_client(tmp_path)
    snapshot = list(SKILLS)
    old = (config.evolution_enabled, config.evolution_proposable)
    register_skill(Skill(name="dice", prompt="Roll dice honestly."))
    configure(evolution_enabled=True, evolution_proposable=["greet.md"])
    try:
        body = client.get("/admin/v1/proposals").json()
        # The operator sees the same allowlist the assistant proposes against.
        assert body["proposable"] == ["greet.md", "skills/dice.md"]
    finally:
        SKILLS[:] = snapshot
        configure(evolution_enabled=old[0], evolution_proposable=old[1])
