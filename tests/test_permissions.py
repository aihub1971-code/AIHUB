import pytest
from fastapi import HTTPException
from app.permissions import is_allowed, require_capability
from app.registry import RETIRED_AGENT_IDS, get_agents, get_permissions

SEARCH_AGENTS = {
    "ubi-research", "iran-news", "uae-eu-trade-talks", "geopolitical-morning",
    "education-observatory", "historical-reading-companion", "economic-cultural-briefing",
}


def test_all_active_agents_have_both_retrieval_tools_and_retired_agents_have_neither():
    for agent_id in SEARCH_AGENTS | RETIRED_AGENT_IDS:
        assert is_allowed(agent_id, "tools", "web_search") is (agent_id in SEARCH_AGENTS)
        assert is_allowed(agent_id, "tools", "newsdata") is (agent_id in SEARCH_AGENTS)
        assert not is_allowed(agent_id, "external_actions", "send_message")


def test_active_permissions_match_the_active_registry():
    active_ids = {agent["id"] for agent in get_agents()}
    assert set(get_permissions()["agents"]) == active_ids == SEARCH_AGENTS
    for agent_id in active_ids:
        for action in ("inspect", "read_skill", "submit_task"):
            require_capability(agent_id, "actions", action)


@pytest.mark.parametrize("agent_id", sorted(RETIRED_AGENT_IDS))
@pytest.mark.parametrize(("capability_type", "capability"), [
    ("actions", "inspect"), ("actions", "read_skill"), ("actions", "submit_task"),
    ("tools", "web_search"), ("tools", "newsdata"), ("external_actions", "send_message"),
])
def test_retired_assistants_cannot_launch_or_access_capabilities(agent_id, capability_type, capability):
    assert not is_allowed(agent_id, capability_type, capability)
    with pytest.raises(HTTPException) as error:
        require_capability(agent_id, capability_type, capability)
    assert error.value.status_code == 404


def test_unlisted_tool_is_denied():
    with pytest.raises(HTTPException) as error:
        require_capability("iran-news", "tools", "code_interpreter")
    assert error.value.status_code == 403

def test_unknown_agent_is_not_authorized():
    with pytest.raises(HTTPException) as error:
        require_capability("unknown-agent", "actions", "inspect")
    assert error.value.status_code == 404
