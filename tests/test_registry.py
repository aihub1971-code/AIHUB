import json

import pytest

from app.registry import AGENTS_PATH, RETIRED_AGENT_IDS, get_agents, get_agent, read_skill


ACTIVE_AGENT_IDS = [
    "ubi-research", "iran-news", "uae-eu-trade-talks", "geopolitical-morning",
    "education-observatory", "historical-reading-companion", "story-moodboard",
    "economic-cultural-briefing", "decision-briefing",
]


def test_registry_has_nine_active_agents_in_original_order():
    agents = get_agents()
    assert [agent["id"] for agent in agents] == ACTIVE_AGENT_IDS
    assert [agent["number"] for agent in agents] == [f"{number:02}" for number in range(1, 10)]
    assert all(agent["enabled"] is True for agent in agents)
    assert json.loads(AGENTS_PATH.read_text(encoding="utf-8"))["agent_count"] == len(agents) == 9


@pytest.mark.parametrize(("agent_id", "skill_id"), [
    ("mom-agent", "instructions"),
    ("mom-agent", "manage-project-meetings"),
    ("whatsapp-briefing", "whatsapp-briefing"),
    ("literary-translation", "literary-translation"),
    ("pre-meeting-briefing", "pre-meeting-briefing"),
])
def test_retired_assistants_are_unregistered_and_their_skills_unavailable(agent_id, skill_id):
    assert get_agent(agent_id) is None
    assert read_skill(agent_id, skill_id) is None


def test_archive_identities_do_not_reenable_retired_assistants():
    assert RETIRED_AGENT_IDS == {
        "mom-agent", "whatsapp-briefing", "literary-translation", "pre-meeting-briefing",
    }
    assert RETIRED_AGENT_IDS.isdisjoint(agent["id"] for agent in get_agents())

def test_source_skill_text_is_available():
    content = read_skill("iran-news", "iran-news")
    assert content and "Iran" in content

def test_all_skill_paths_are_present_and_content_hashes_match():
    import hashlib
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for agent in get_agents():
        for skill in agent["skills"]:
            content = (root / skill["path"]).read_bytes()
            assert hashlib.sha256(content).hexdigest() == skill["sha256"]
