"""Validation helpers for public API client routes."""

import re
from typing import Dict, Optional, Tuple

from fastapi import HTTPException

from src.agent import AgentStatus

ERROR_UNAUTHORIZED = "UNAUTHORIZED"
ERROR_VALIDATION_FAILED = "VALIDATION_FAILED"

_GROUP_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_AGENT_TYPE_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_-]{0,63}\.[A-Za-z][A-Za-z0-9_-]{0,63}$"
)
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def public_api_error(
    status_code: int,
    code: str,
    message: str,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def validate_auth_header(authorization: str) -> None:
    if (
        not authorization.startswith("Bearer ")
        or not authorization[7:].strip()
    ):
        raise public_api_error(401, ERROR_UNAUTHORIZED, "Unauthorized")


def validate_agent_filters(
    status: Optional[str],
    group: Optional[str],
) -> Tuple[Optional[AgentStatus], Optional[str]]:
    status_filter = None
    if status is not None:
        try:
            status_filter = AgentStatus(status)
        except ValueError:
            raise public_api_error(
                400,
                ERROR_VALIDATION_FAILED,
                "Invalid agent status filter.",
            )

    if group is not None:
        group = group.strip()
        if not _GROUP_RE.fullmatch(group):
            raise public_api_error(
                400,
                ERROR_VALIDATION_FAILED,
                "Invalid agent group filter.",
            )

    return status_filter, group


def validate_agent_registration(
    name: Optional[str],
    agent_type: Optional[str],
    config: Optional[Dict],
) -> Tuple[str, str, Optional[Dict]]:
    if name is None or not _AGENT_NAME_RE.fullmatch(name.strip()):
        raise public_api_error(
            400,
            ERROR_VALIDATION_FAILED,
            "Invalid agent name.",
        )
    if agent_type is None or not _AGENT_TYPE_RE.fullmatch(agent_type.strip()):
        raise public_api_error(
            400,
            ERROR_VALIDATION_FAILED,
            "Invalid agent type.",
        )
    if config is not None and not isinstance(config, dict):
        raise public_api_error(
            400,
            ERROR_VALIDATION_FAILED,
            "Invalid agent config.",
        )
    return name.strip(), agent_type.strip(), config


def validate_agent_id(agent_id: str) -> str:
    if not _AGENT_ID_RE.fullmatch(agent_id):
        raise public_api_error(
            400,
            ERROR_VALIDATION_FAILED,
            "Invalid agent id.",
        )
    return agent_id
