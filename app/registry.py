"""Loads the checked-in agent and permission registries."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from app.config import PROJECT_ROOT

AGENTS_PATH = PROJECT_ROOT / "agents.json"
PERMISSIONS_PATH = PROJECT_ROOT / "config" / "permissions.json"

# Historical reports can retain these identities without re-enabling an
# assistant, a profile, a skill, or any capability in the active registry.
RETIRED_AGENT_IDS = frozenset({
    "mom-agent", "whatsapp-briefing", "literary-translation", "pre-meeting-briefing",
})

def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value

def get_agents() -> list[dict[str, Any]]:
    items = _load_json(AGENTS_PATH).get("agents", [])
    if not isinstance(items, list):
        raise ValueError("agents.json must include an agents list")
    ids = [item.get("id") for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate agent id in agents.json")
    return items

def get_agent(agent_id: str) -> dict[str, Any] | None:
    return next((item for item in get_agents() if item.get("id") == agent_id), None)

def get_permissions() -> dict[str, Any]:
    return _load_json(PERMISSIONS_PATH)

def read_skill(agent_id: str, skill_id: str) -> str | None:
    agent = get_agent(agent_id)
    if agent is None:
        return None
    skill = next((s for s in agent.get("skills", []) if s.get("id") == skill_id), None)
    if skill is None:
        return None
    path = (PROJECT_ROOT / skill["path"]).resolve()
    # Defense in depth against path traversal if registry is accidentally edited.
    if PROJECT_ROOT.resolve() not in path.parents:
        raise ValueError("Skill path is outside the project root")
    return path.read_text(encoding="utf-8")
