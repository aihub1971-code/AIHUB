from app.registry import get_agents, get_agent, read_skill

def test_registry_has_13_unique_agents():
    agents = get_agents()
    ids = [a["id"] for a in agents]
    assert len(agents) == 13
    assert len(ids) == len(set(ids))

def test_meeting_agent_keeps_both_original_skills():
    agent = get_agent("mom-agent")
    assert agent is not None
    assert {s["id"] for s in agent["skills"]} == {"instructions", "manage-project-meetings"}

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
