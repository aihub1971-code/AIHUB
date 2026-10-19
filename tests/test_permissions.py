import pytest
from fastapi import HTTPException
from app.permissions import is_allowed, require_capability

def test_bootstrap_has_no_tools_enabled():
    for agent_id in [
        "ubi-research", "iran-news", "uae-eu-trade-talks", "geopolitical-morning",
        "education-observatory", "historical-reading-companion", "story-moodboard",
        "economic-cultural-briefing", "decision-briefing", "mom-agent",
        "whatsapp-briefing", "literary-translation", "pre-meeting-briefing",
    ]:
        assert not is_allowed(agent_id, "tools", "web_search")
        assert not is_allowed(agent_id, "external_actions", "send_message")

def test_unlisted_tool_is_denied():
    with pytest.raises(HTTPException) as error:
        require_capability("iran-news", "tools", "web_search")
    assert error.value.status_code == 403

def test_unknown_agent_is_not_authorized():
    with pytest.raises(HTTPException) as error:
        require_capability("unknown-agent", "actions", "inspect")
    assert error.value.status_code == 404
