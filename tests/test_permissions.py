import pytest
from fastapi import HTTPException
from app.permissions import is_allowed, require_capability

SEARCH_AGENTS = {
    "ubi-research", "iran-news", "uae-eu-trade-talks", "geopolitical-morning",
    "education-observatory", "historical-reading-companion", "story-moodboard",
    "economic-cultural-briefing", "decision-briefing", "pre-meeting-briefing",
}


def test_only_research_agents_have_hosted_web_search():
    for agent_id in [
        "ubi-research", "iran-news", "uae-eu-trade-talks", "geopolitical-morning",
        "education-observatory", "historical-reading-companion", "story-moodboard",
        "economic-cultural-briefing", "decision-briefing", "mom-agent",
        "whatsapp-briefing", "literary-translation", "pre-meeting-briefing",
    ]:
        assert is_allowed(agent_id, "tools", "web_search") is (agent_id in SEARCH_AGENTS)
        assert is_allowed(agent_id, "tools", "newsdata") is (agent_id == "iran-news")
        assert not is_allowed(agent_id, "external_actions", "send_message")

def test_unlisted_tool_is_denied():
    with pytest.raises(HTTPException) as error:
        require_capability("iran-news", "tools", "code_interpreter")
    assert error.value.status_code == 403

def test_unknown_agent_is_not_authorized():
    with pytest.raises(HTTPException) as error:
        require_capability("unknown-agent", "actions", "inspect")
    assert error.value.status_code == 404
