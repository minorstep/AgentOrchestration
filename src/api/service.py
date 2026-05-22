"""Shared API service operations and validation."""

from typing import Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException

from src.agent import AgentRegistry, AgentStatus


def _validation_error(field: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "code": "validation_error",
            "field": field,
            "message": message,
        },
    )


def _require_text(value: Optional[str], field: str) -> str:
    if value is None or not value.strip():
        raise _validation_error(field, f"{field} is required")
    return value.strip()


def _parse_agent_id(agent_id: str) -> str:
    try:
        UUID(agent_id)
    except (TypeError, ValueError):
        raise _validation_error("agent_id", "agent_id must be a valid UUID")
    return agent_id


def _parse_status(status: Optional[str]) -> Optional[AgentStatus]:
    if status is None:
        return None
    try:
        return AgentStatus(status)
    except ValueError:
        error = _validation_error(
            "status",
            "status must be a known agent status",
        )
        error.detail["allowed"] = [item.value for item in AgentStatus]
        raise error


class AgentAPIService:
    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    def list_agents(
        self,
        status: Optional[str] = None,
        group: Optional[str] = None,
    ) -> List[Dict]:
        status_filter = _parse_status(status)
        return self.registry.list(status=status_filter, group=group)

    def register_agent(
        self,
        name: Optional[str],
        agent_type: Optional[str],
        config: Optional[Dict] = None,
    ) -> str:
        valid_name = _require_text(name, "name")
        valid_agent_type = _require_text(agent_type, "agent_type")
        return self.registry.register(valid_name, valid_agent_type, config)

    def get_agent(self, agent_id: str) -> Dict:
        valid_agent_id = _parse_agent_id(agent_id)
        agent = self.registry.get(valid_agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent

    def delete_agent(self, agent_id: str) -> None:
        valid_agent_id = _parse_agent_id(agent_id)
        if not self.registry.delete(valid_agent_id):
            raise HTTPException(status_code=404, detail="Agent not found")

    def start_agent(self, agent_id: str) -> None:
        valid_agent_id = _parse_agent_id(agent_id)
        updated = self.registry.update_status(
            valid_agent_id,
            AgentStatus.RUNNING,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Agent not found")

    def stop_agent(self, agent_id: str) -> None:
        valid_agent_id = _parse_agent_id(agent_id)
        updated = self.registry.update_status(
            valid_agent_id,
            AgentStatus.PAUSED,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Agent not found")

    def count_agents(self) -> int:
        return self.registry.count()
