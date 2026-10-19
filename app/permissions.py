"""Default-deny authorization for agent actions and tool calls."""
from __future__ import annotations
from fastapi import HTTPException, status
from app.registry import get_agent, get_permissions

def is_allowed(agent_id: str, capability_type: str, capability: str) -> bool:
    agent = get_agent(agent_id)
    if agent is None:
        return False
    permission_doc = get_permissions()
    record = permission_doc.get("agents", {}).get(agent_id)
    if not isinstance(record, dict):
        return False
    allowlist = record.get(capability_type, [])
    return isinstance(allowlist, list) and capability in allowlist

def require_capability(agent_id: str, capability_type: str, capability: str) -> None:
    if get_agent(agent_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown agent")
    if not is_allowed(agent_id, capability_type, capability):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "capability_not_allowed", "agent_id": agent_id, "capability_type": capability_type, "capability": capability},
        )
